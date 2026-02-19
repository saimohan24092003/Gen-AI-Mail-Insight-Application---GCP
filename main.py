import os
import base64
import json
import re

from html import unescape
from flask import Flask, render_template, jsonify, request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.utils import parseaddr

import vertexai
from vertexai.generative_models import GenerativeModel
import functions_framework


# =========================
# FLASK APP
# =========================
flask_app = Flask(__name__)


# =========================
# ENV
# =========================
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN")


# =========================
# VERTEX AI
# =========================
vertexai.init(
    project="global-bee-486909-m4",
    location="us-central1"
)

model = GenerativeModel("gemini-2.0-flash-001")


# =========================
# GREETINGS + SIGNATURES
# =========================
def get_greeting(language):
    return {
        "English":"Hi,",
        "Hindi":"नमस्ते,",
        "Telugu":"హాయ్,",
        "Kannada":"ನಮಸ್ಕಾರ,",
        "Malayalam":"നമസ്കാരം,"
    }.get(language,"Hi,")


def get_signature(language):
    return {
        "English":"Best regards,\nSai",
        "Hindi":"सादर,\nSai",
        "Telugu":"శుభాకాంక్షలతో,\nSai",
        "Kannada":"ಶುಭಾಶಯಗಳೊಂದಿಗೆ,\nSai",
        "Malayalam":"ആശംസകളോടെ,\nSai"
    }.get(language,"Best regards,\nSai")


# =========================
# GMAIL SERVICE
# =========================
def get_gmail_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/gmail.modify"]
    )
    return build("gmail","v1",credentials=creds)


# =========================
# CLEAN HTML
# =========================
def clean_html(html):

    html = re.sub(r'<(script|style).*?>.*?</\1>','',html,flags=re.S)
    html = re.sub(r'<br\s*/?>','\n',html)
    html = re.sub(r'</p>','\n\n',html)

    text = re.sub(r'<[^>]+>',' ',html)
    text = unescape(text)

    text = re.sub(r'http\S+','',text)
    text = re.sub(r'\s+',' ',text)

    return text.strip()


# =========================
# EXTRACT BODY
# =========================
def extract_body(payload):

    if "parts" in payload:
        for part in payload["parts"]:
            mime = part.get("mimeType","")

            if mime=="text/html" and "data" in part["body"]:
                html = base64.urlsafe_b64decode(
                    part["body"]["data"]
                ).decode(errors="ignore")
                return clean_html(html)

            if mime=="text/plain" and "data" in part["body"]:
                return base64.urlsafe_b64decode(
                    part["body"]["data"]
                ).decode(errors="ignore")

    if "data" in payload.get("body",{}):
        html = base64.urlsafe_b64decode(
            payload["body"]["data"]
        ).decode(errors="ignore")
        return clean_html(html)

    return ""


# =========================
# HOME (INBOX)
# =========================
@flask_app.route("/")
def home():

    svc = get_gmail_service()

    res = svc.users().messages().list(
        userId="me",
        maxResults=15
    ).execute()

    msgs = res.get("messages",[])
    emails=[]

    my_email="samplesgenai189@gmail.com"

    for m in msgs:

        msg = svc.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["Subject","From"]
        ).execute()

        headers = msg["payload"]["headers"]

        subject = next((h["value"] for h in headers if h["name"]=="Subject"),"No Subject")
        sender_raw = next((h["value"] for h in headers if h["name"]=="From"),"Unknown")

        sender_email = parseaddr(sender_raw)[1]
        labels = msg.get("labelIds",[])

        if sender_email==my_email or "SENT" in labels:
            continue

        # ✅ STRICT urgency classification
        try:
            r = model.generate_content(f"""
Return ONLY one word: Low, Medium, or High.

Email subject:
{subject}
""")

            urgency_raw = r.text.strip().lower()

            if "high" in urgency_raw:
                urgency="High"
            elif "low" in urgency_raw:
                urgency="Low"
            else:
                urgency="Medium"

        except:
            urgency="Medium"

        emails.append({
            "id":m["id"],
            "subject":subject,
            "from":sender_raw,
            "urgency":urgency
        })

    return render_template("index.html",emails=emails)


# =========================
# ANALYZE EMAIL
# =========================
@flask_app.route("/analyze/<msg_id>")
def analyze(msg_id):

    svc=get_gmail_service()

    msg=svc.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()

    headers=msg["payload"]["headers"]

    subject=next((h["value"] for h in headers if h["name"]=="Subject"),"No Subject")
    sender=next((h["value"] for h in headers if h["name"]=="From"),"Unknown")

    body=extract_body(msg["payload"])

    language=request.args.get("lang","English")

    prompt=f"""
Return JSON:

{{
 "urgency":"Low/Medium/High",
 "sentiment":"Positive/Neutral/Negative",
 "reply":"plain text reply only"
}}

Rules:
- Reply in {language}
- No greeting
- No signature

EMAIL:
{body}
"""

    r=model.generate_content(prompt)

    try:
        text=r.text.strip().replace("```json","").replace("```","")
        ai=json.loads(text)
    except:
        ai={
            "urgency":"Medium",
            "sentiment":"Neutral",
            "reply":r.text.strip()
        }

    greeting=get_greeting(language)
    signature=get_signature(language)

    reply_text=re.sub(r'Best regards.*','',ai["reply"],flags=re.I).strip()

    final_reply=f"{greeting}\n\n{reply_text}\n\n{signature}"

    return jsonify({
        "subject":subject,
        "from":sender,
        "body":body[:2000],
        "urgency":ai["urgency"],
        "sentiment":ai["sentiment"],
        "reply":final_reply
    })


# =========================
# SEND REPLY
# =========================
@flask_app.route("/send_reply",methods=["POST"])
def send_reply():

    d=request.json
    msg_id=d["msg_id"]
    reply=d["reply"]

    svc=get_gmail_service()

    m=svc.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=["From","Subject","Message-ID"]
    ).execute()

    headers=m["payload"]["headers"]
    thread_id=m["threadId"]

    to_email=parseaddr(
        next(h["value"] for h in headers if h["name"]=="From")
    )[1]

    subject=next((h["value"] for h in headers if h["name"]=="Subject"),"No Subject")

    message_id=next((h["value"] for h in headers if h["name"]=="Message-ID"),None)

    if not subject.lower().startswith("re:"):
        subject=f"Re: {subject}"

    raw=(
        f"To: {to_email}\r\n"
        f"Subject: {subject}\r\n"
        f"In-Reply-To: {message_id}\r\n"
        f"References: {message_id}\r\n"
        f"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
        f"{reply}"
    )

    encoded=base64.urlsafe_b64encode(raw.encode()).decode()

    svc.users().messages().send(
        userId="me",
        body={"raw":encoded,"threadId":thread_id}
    ).execute()

    return jsonify({"status":"sent"})


# =========================
# ENTRYPOINT
# =========================
@functions_framework.http
def app(request):
    return flask_app(request.environ,lambda *args:None)
