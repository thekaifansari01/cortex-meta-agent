import os
import re
import time
import base64
import mimetypes
import webbrowser
import json
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.logger.logger import logger
from core.security import load_decrypted_token, save_encrypted_token

try:
    from core.voice.tts import speak
except ImportError:
    def speak(text):
        logger.warning(f"TTS not available, speaking via log: {text}")

load_dotenv()

SCOPES = ['https://mail.google.com/', 'https://www.googleapis.com/auth/pubsub']
BASE_DIR = Path(__file__).resolve().parent.parent.parent
COOKIES_DIR = BASE_DIR / "Data" / "SessionCookies"
COOKIES_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_PATH = COOKIES_DIR / "token.enc"
ATTACHMENTS_DIR = COOKIES_DIR / "EmailAttachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

import threading as _threading
_service_cache = None
_service_lock = _threading.Lock()


def _strip_html(html: str) -> str:
    if not html:
        return ""
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</div>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</li>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n+", "\n\n", html)
    return html.strip()


def _safe_filename(name: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]', "_", name or "attachment")
    clean = clean.strip() or "attachment"
    return clean[:120]


def _unique_path(directory: Path, filename: str) -> Path:
    safe = _safe_filename(filename)
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem, ext = os.path.splitext(safe)
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def authenticate_gmail(interactive: bool = True):
    logger.info("Authenticating Gmail...")
    creds = None
    if TOKEN_PATH.exists():
        try:
            token_info = load_decrypted_token(str(TOKEN_PATH))
            if token_info:
                creds = Credentials.from_authorized_user_info(token_info, SCOPES)
                logger.debug("Gmail token found.")
        except Exception:
            creds = None
            try:
                TOKEN_PATH.unlink()
            except Exception:
                pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                save_encrypted_token(json.loads(creds.to_json()), str(TOKEN_PATH))
                logger.info("Gmail token refreshed.")
            except Exception:
                creds = None
                try:
                    TOKEN_PATH.unlink()
                except Exception:
                    pass

        if (not creds or not creds.valid) and interactive:
            import uuid
            secure_session = str(uuid.uuid4())
            logger.info("Opening browser for Gmail OAuth...")
            webbrowser.open(f"https://jarvis-os-agent.vercel.app/api/oauth/start?service=gmail&state={secure_session}")
            timeout = 120
            start_time = time.time()
            while time.time() - start_time < timeout:
                if TOKEN_PATH.exists():
                    try:
                        token_info = load_decrypted_token(str(TOKEN_PATH))
                        if token_info:
                            creds = Credentials.from_authorized_user_info(token_info, SCOPES)
                            if creds and creds.valid:
                                logger.info("Gmail OAuth completed.")
                                break
                    except Exception:
                        pass
                time.sleep(3)

    if creds and creds.valid:
        global _service_cache
        with _service_lock:
            if _service_cache is None:
                _service_cache = build('gmail', 'v1', credentials=creds, cache_discovery=False)
            return _service_cache
    logger.error("Gmail authentication failed.")
    return None


def send_email(to_address, subject, body, attachment_path=None):
    logger.info(f"Sending email to {to_address} | Subject: {subject}")
    if attachment_path and not os.path.exists(attachment_path):
        logger.warning(f"Attachment not found: {attachment_path}")
        return False

    try:
        service = authenticate_gmail()
        if not service:
            return False

        message = EmailMessage()
        message.set_content(body)
        message['To'] = to_address
        message['From'] = 'me'
        message['Subject'] = subject

        if attachment_path and os.path.exists(attachment_path):
            ctype, encoding = mimetypes.guess_type(attachment_path)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)

            with open(attachment_path, 'rb') as fp:
                message.add_attachment(
                    fp.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=os.path.basename(attachment_path),
                )

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': encoded_message}

        service.users().messages().send(userId="me", body=create_message).execute()
        logger.info(f"Email sent successfully to {to_address}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def _walk_payload(part, plain_body, html_body, attachments, service, msg_id, _depth=0):
    if _depth > 20:
        return
    mime_type = part.get("mimeType", "")
    parts = part.get("parts", [])

    if parts:
        for p in parts:
            _walk_payload(p, plain_body, html_body, attachments, service, msg_id, _depth + 1)
        return

    body_data = part.get("body", {})
    data = body_data.get("data")
    filename = part.get("filename", "")

    if filename and data and (part.get("body") or {}).get("attachmentId"):
        attachment_id = part["body"]["attachmentId"]
        size = int(part["body"].get("size", 0))
        if size > MAX_ATTACHMENT_BYTES:
            logger.warning(f"Skipping attachment '{filename}' ({size} bytes) - exceeds limit.")
            return
        try:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=attachment_id
            ).execute()
            file_data = base64.urlsafe_b64decode(attachment["data"])
            save_path = _unique_path(ATTACHMENTS_DIR, filename)
            with open(save_path, "wb") as f:
                f.write(file_data)
            attachments.append(str(save_path))
        except Exception as e:
            logger.warning(f"Failed to download attachment '{filename}': {e}")
        return

    if not data:
        return

    try:
        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return

    if mime_type == "text/plain":
        plain_body.append(decoded)
    elif mime_type == "text/html":
        html_body.append(decoded)


def _extract_email_content(service, msg_id, full_msg):
    headers = full_msg.get("payload", {}).get("headers", [])
    header_map = {h.get("name", "").lower(): h.get("value", "") for h in headers}

    from_name, from_email = parseaddr(header_map.get("from", ""))
    subject = header_map.get("subject", "(No Subject)")

    plain_body = []
    html_body = []
    attachments = []

    payload = full_msg.get("payload", {})
    if payload:
        _walk_payload(payload, plain_body, html_body, attachments, service, msg_id)

    if plain_body:
        body_text = "\n".join(plain_body)
    elif html_body:
        body_text = _strip_html("\n".join(html_body))
    else:
        body_text = full_msg.get("snippet", "")

    body_text = body_text.strip()
    return from_name or "", from_email or "", subject, body_text, attachments, None


def _mark_as_read(service, msg_id):
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def fetch_emails_by_date(start_date, end_date=None, query=None, max_results=50, mark_as_read_flag=False):
    service = authenticate_gmail(interactive=False)
    if not service:
        return {
            "success": False,
            "error": "Gmail authentication failed.",
            "count": 0,
            "emails": [],
            "saved_attachments": [],
        }

    try:
        start_dt = datetime.strptime(str(start_date).strip(), "%Y-%m-%d")
    except (ValueError, TypeError):
        return {
            "success": False,
            "error": f"Invalid start_date '{start_date}'. Expected format YYYY-MM-DD.",
            "count": 0,
            "emails": [],
            "saved_attachments": [],
        }

    if end_date:
        try:
            end_dt = datetime.strptime(str(end_date).strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            return {
                "success": False,
                "error": f"Invalid end_date '{end_date}'. Expected format YYYY-MM-DD.",
                "count": 0,
                "emails": [],
                "saved_attachments": [],
            }
    else:
        end_dt = start_dt

    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    before_dt = end_dt + timedelta(days=1)
    gmail_query = f"after:{start_dt.strftime('%Y/%m/%d')} before:{before_dt.strftime('%Y/%m/%d')}"

    if query and str(query).strip():
        gmail_query = f"{gmail_query} {str(query).strip()}"

    try:
        max_results = int(max_results)
    except (ValueError, TypeError):
        max_results = 50

    if max_results <= 0:
        max_results = 10000
    max_results = min(max_results, 10000)

    all_emails = []
    all_attachments = []
    page_token = None
    remaining = max_results
    MAX_PER_PAGE = 500

    logger.info(f"Fetching emails | query='{gmail_query}' | max={max_results}")

    try:
        while remaining > 0:
            fetch_count = min(remaining, MAX_PER_PAGE)
            list_params = {
                "userId": "me",
                "q": gmail_query,
                "maxResults": fetch_count,
            }
            if page_token:
                list_params["pageToken"] = page_token

            results = service.users().messages().list(**list_params).execute()
            messages = results.get("messages", [])

            if not messages:
                break

            for msg_ref in messages:
                msg_id = msg_ref.get("id")
                if not msg_id:
                    continue

                try:
                    full_msg = service.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute()

                    name, email, sub, body, saved_attachments, _ = _extract_email_content(
                        service, msg_id, full_msg
                    )

                    internal_date_raw = full_msg.get("internalDate", 0)
                    try:
                        internal_date = int(internal_date_raw) if internal_date_raw else 0
                    except (ValueError, TypeError):
                        internal_date = 0
                    if internal_date > 0:
                        try:
                            date_str = datetime.fromtimestamp(internal_date / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OSError, OverflowError):
                            date_str = "Unknown"
                    else:
                        date_str = "Unknown"

                    all_emails.append({
                        "from_name": name,
                        "from_email": email,
                        "subject": sub,
                        "body": body,
                        "date": date_str,
                        "attachments": list(saved_attachments) if saved_attachments else [],
                        "msg_id": msg_id,
                    })

                    if saved_attachments:
                        all_attachments.extend(saved_attachments)

                    if mark_as_read_flag:
                        try:
                            _mark_as_read(service, msg_id)
                        except Exception as mark_err:
                            logger.warning(f"Failed to mark {msg_id} as read: {mark_err}")

                except Exception as msg_err:
                    logger.warning(f"Failed to fetch message {msg_id}: {msg_err}")
                    continue

            remaining -= len(messages)
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        logger.info(f"Fetch complete | count={len(all_emails)} | attachments={len(all_attachments)}")

        return {
            "success": True,
            "count": len(all_emails),
            "emails": all_emails,
            "saved_attachments": all_attachments,
            "date_range": {
                "start": start_dt.strftime("%Y-%m-%d"),
                "end": end_dt.strftime("%Y-%m-%d"),
            },
        }

    except Exception as e:
        logger.error(f"Failed to fetch emails: {e}")
        return {
            "success": False,
            "error": str(e),
            "count": len(all_emails),
            "emails": all_emails,
            "saved_attachments": all_attachments,
        }


if __name__ == "__main__":
    auth = authenticate_gmail()