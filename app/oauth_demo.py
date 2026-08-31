"""Standalone Google OAuth demo for the verification recording. Port 8085 only."""

from __future__ import annotations

import base64
import html
import os
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PORT = 8085
REDIRECT_URI = "http://localhost:8085/oauth/callback"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
)
SHOWING_TITLE = "Showing \u2014 123 Main St"
HANDLED_LABEL = "Yamin/Handled"

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

app = FastAPI(title="Yamin", docs_url=None, redoc_url=None)
_oauth_state = ""


class DemoError(Exception):
    pass


def client_path() -> Path:
    raw = os.environ.get("YAMIN_OAUTH_CLIENT", "")
    if not raw:
        raise DemoError("YAMIN_OAUTH_CLIENT is not set")
    path = Path(raw)
    if not path.is_file():
        raise DemoError("YAMIN_OAUTH_CLIENT does not point to a file")
    return path


def token_path() -> Path:
    raw = os.environ.get("YAMIN_DEMO_TOKEN", "")
    if not raw:
        raise DemoError("YAMIN_DEMO_TOKEN is not set")
    return Path(raw)


def sheet_id() -> str:
    raw = os.environ.get("YAMIN_DEMO_SHEET_ID", "")
    if not raw:
        raise DemoError("YAMIN_DEMO_SHEET_ID is not set")
    return raw


def page(title: str, body: str) -> HTMLResponse:
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{
  font-family: Georgia, "Times New Roman", serif;
  max-width: 720px;
  margin: 2rem auto;
  padding: 0 1.25rem 3rem;
  line-height: 1.5;
  color: #222;
}}
header {{
  background: #24427C;
  color: #fff;
  padding: 1.25rem 1.5rem;
  margin: 0 -1.25rem 1.5rem;
}}
header h1 {{ margin: 0; font-size: 1.6rem; }}
header p {{ margin: 0.35rem 0 0; opacity: 0.9; }}
a.btn, button {{
  display: inline-block;
  background: #24427C;
  color: #fff;
  border: 0;
  padding: 0.7rem 1.2rem;
  font: inherit;
  cursor: pointer;
  text-decoration: none;
  margin: 0 0.5rem 0.75rem 0;
}}
form.demo {{ display: inline; }}
.card {{
  border: 1px solid #c5c9d4;
  padding: 1rem 1.1rem;
  margin: 1rem 0;
  background: #f7f8fb;
}}
.card h3 {{ margin: 0 0 0.4rem; }}
.result {{
  border-left: 4px solid #24427C;
  padding: 0.75rem 1rem;
  margin: 1rem 0;
  background: #eef2fa;
}}
.err {{ border-left-color: #8a1f1f; background: #f8eeee; }}
.muted {{ color: #555; }}
</style>
</head>
<body>
<header><h1>Yamin</h1><p>Google account connect</p></header>
{body}
</body>
</html>
"""
    return HTMLResponse(doc)


def connect_body(error: str = "") -> str:
    err = f'<div class="result err">{html.escape(error)}</div>' if error else ""
    return f"""
{err}
<p>Connect the Google account used for the verification recording.</p>
<p><a class="btn" href="/oauth/start">Connect Google</a></p>
"""


def connected_body(result: str = "", error: str = "") -> str:
    box = ""
    if error:
        box = f'<div class="result err">{error}</div>'
    elif result:
        box = f'<div class="result">{result}</div>'
    return f"""
<p>Connected. Each button below runs one scope against the connected account.</p>
{box}
<p>
<form class="demo" method="post" action="/demo/inbox"><button>Scan inbox</button></form>
<form class="demo" method="post" action="/demo/digest"><button>Send digest</button></form>
<form class="demo" method="post" action="/demo/showing"><button>Book showing</button></form>
<form class="demo" method="post" action="/demo/sheet"><button>Update lead sheet</button></form>
<form class="demo" method="post" action="/demo/drive"><button>Open lead file</button></form>
</p>
<p class="muted"><a href="/oauth/start">Connect a different account</a></p>
"""


def make_flow() -> Flow:
    return Flow.from_client_secrets_file(
        str(client_path()),
        scopes=list(SCOPES),
        redirect_uri=REDIRECT_URI,
    )


def save_creds(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    path.chmod(0o600)


def load_creds() -> Credentials:
    path = token_path()
    if not path.is_file():
        raise DemoError("not connected")
    creds = Credentials.from_authorized_user_file(str(path), list(SCOPES))
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_creds(creds)
    if not creds.valid:
        raise DemoError("saved Google credentials are not valid; connect again")
    return creds


def header_value(msg: dict, name: str) -> str:
    for item in msg.get("payload", {}).get("headers", []):
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def addr_of(from_header: str) -> str:
    return parseaddr(from_header)[1].lower()


def thread_replied_by(thread: dict, me: str) -> bool:
    for msg in thread.get("messages", []):
        if addr_of(header_value(msg, "From")) == me:
            return True
    return False


def gmail_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def ensure_label(gmail, name: str) -> str:
    for label in gmail.users().labels().list(userId="me").execute().get("labels", []):
        if label.get("name") == name:
            return label["id"]
    created = gmail.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def find_oldest_unanswered(gmail) -> dict:
    me = gmail.users().getProfile(userId="me").execute()["emailAddress"].lower()
    listed = gmail.users().messages().list(
        userId="me", q="in:inbox -from:me", maxResults=100,
    ).execute().get("messages", [])
    if not listed:
        raise DemoError("no inbound inbox messages in the scanned window")
    details = []
    for item in listed:
        details.append(
            gmail.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        )
    details.sort(key=lambda m: int(m.get("internalDate", "0")))
    threads: dict[str, dict] = {}
    for msg in details:
        tid = msg["threadId"]
        if tid not in threads:
            threads[tid] = gmail.users().threads().get(
                userId="me",
                id=tid,
                format="metadata",
                metadataHeaders=["From"],
            ).execute()
        if not thread_replied_by(threads[tid], me):
            return msg
    raise DemoError("no unanswered inbound email in the scanned window")


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    try:
        load_creds()
    except DemoError:
        return page("Yamin", connect_body())
    return page("Yamin", connected_body())


@app.get("/oauth/start")
def oauth_start():
    global _oauth_state
    try:
        flow = make_flow()
    except DemoError as exc:
        return page("Yamin", connect_body(str(exc)))
    except Exception:
        return page("Yamin", connect_body("OAuth client file could not be used"))
    authorization_url, _oauth_state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return RedirectResponse(authorization_url, status_code=302)


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request) -> HTMLResponse:
    global _oauth_state
    if request.query_params.get("error"):
        return page("Yamin", connect_body("Google returned an error; connect was not completed"))
    state = request.query_params.get("state", "")
    if not _oauth_state or state != _oauth_state:
        return page("Yamin", connect_body("OAuth state mismatch; start connect again"))
    try:
        flow = make_flow()
        flow.fetch_token(authorization_response=str(request.url))
        save_creds(flow.credentials)
    except DemoError as exc:
        return page("Yamin", connect_body(str(exc)))
    except Exception:
        return page("Yamin", connect_body("Could not finish the OAuth exchange"))
    _oauth_state = ""
    return page("Yamin", connected_body(result="Google account connected."))


def _run(action):
    try:
        creds = load_creds()
        return page("Yamin", connected_body(result=action(creds)))
    except DemoError as exc:
        try:
            load_creds()
        except DemoError:
            return page("Yamin", connect_body(str(exc)))
        return page("Yamin", connected_body(error=html.escape(str(exc))))
    except HttpError as exc:
        return page("Yamin", connected_body(error=html.escape(f"Google API {exc.status_code}")))
    except Exception:
        return page("Yamin", connected_body(error="The demo action failed"))


@app.post("/demo/inbox", response_class=HTMLResponse)
def demo_inbox() -> HTMLResponse:
    def action(creds: Credentials) -> str:
        gmail = gmail_service(creds)
        msg = find_oldest_unanswered(gmail)
        label_id = ensure_label(gmail, HANDLED_LABEL)
        gmail.users().messages().modify(
            userId="me", id=msg["id"], body={"addLabelIds": [label_id]},
        ).execute()
        subject = html.escape(header_value(msg, "Subject") or "(no subject)")
        sender = html.escape(header_value(msg, "From") or "(unknown)")
        when = html.escape(header_value(msg, "Date") or "")
        return f"""
<div class="card">
<h3>Task</h3>
<p><strong>From unanswered mail</strong></p>
<p>From: {sender}<br>Subject: {subject}<br>Date: {when}</p>
<p>Label applied: {html.escape(HANDLED_LABEL)}</p>
</div>
"""
    return _run(action)


@app.post("/demo/digest", response_class=HTMLResponse)
def demo_digest() -> HTMLResponse:
    def action(creds: Credentials) -> str:
        gmail = gmail_service(creds)
        me = gmail.users().getProfile(userId="me").execute()["emailAddress"]
        body = MIMEText(
            "Yamin task digest\n\n- Follow up on unanswered inbound mail\n"
            "- Showing at 123 Main St\n",
            "plain",
            "utf-8",
        )
        body["To"] = me
        body["From"] = me
        body["Subject"] = "Yamin task digest"
        raw = base64.urlsafe_b64encode(body.as_bytes()).decode()
        sent = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Digest sent to the connected account. Gmail message id {html.escape(sent.get('id', ''))}."
    return _run(action)


@app.post("/demo/showing", response_class=HTMLResponse)
def demo_showing() -> HTMLResponse:
    def action(creds: Credentials) -> str:
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        end = start + timedelta(minutes=30)
        calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
        event = calendar.events().insert(
            calendarId="primary",
            body={
                "summary": SHOWING_TITLE,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            },
        ).execute()
        when = html.escape(event.get("start", {}).get("dateTime", start.isoformat()))
        link = html.escape(event.get("htmlLink", ""))
        eid = html.escape(event.get("id", ""))
        return (
            f"Calendar event created: {html.escape(SHOWING_TITLE)}.<br>"
            f"Start: {when}<br>Event id: {eid}"
            + (f'<br><a href="{link}">Open in Calendar</a>' if link else "")
        )
    return _run(action)


@app.post("/demo/sheet", response_class=HTMLResponse)
def demo_sheet() -> HTMLResponse:
    def action(creds: Credentials) -> str:
        sid = sheet_id()
        today = date.today().isoformat()
        row = ["Yamin Demo", "123 Main St", "Contacted", today]
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        updated = sheets.spreadsheets().values().append(
            spreadsheetId=sid,
            range="A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        rng = html.escape(updated.get("updates", {}).get("updatedRange", ""))
        return (
            f"Appended lead row: {html.escape(', '.join(row))}. "
            f"Updated range {rng}."
        )
    return _run(action)


@app.post("/demo/drive", response_class=HTMLResponse)
def demo_drive() -> HTMLResponse:
    def action(creds: Credentials) -> str:
        sid = sheet_id()
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        meta = drive.files().get(fileId=sid, fields="id,name,modifiedTime").execute()
        name = html.escape(meta.get("name", ""))
        modified = html.escape(meta.get("modifiedTime", ""))
        return f"Lead file: {name}<br>Modified: {modified}"
    return _run(action)
