"""Watch build@pharmpear.com, reply once, and book a clear meeting time.

The mailbox token is read from GMAIL_TOKEN_JSON. Message bodies are not copied
into replies or calendar events.
"""

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data"
TZ = ZoneInfo("America/Chicago")
POLL_SECONDS = 90
MAX_PER_HOUR = 8
MARK = "Ref: pharmpear-desk"

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MEETING = re.compile(
    r"\b(appointment|meeting|schedule|book a|set up a call|set up a time)\b",
    re.I,
)
CLOCK = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b",
    re.I,
)
SKIP_FROM = re.compile(
    r"(mailer-daemon|postmaster|no-?reply|notifications?|donotreply|bounce)",
    re.I,
)
SKIP_SUBJECT = re.compile(
    r"^(undeliverable|delivery status|mail delivery|out of office|automatic reply|auto:)",
    re.I,
)

log = logging.getLogger("pharmpear.mail")
_started = False
_sent_times = []


def load_token():
    raw = os.environ.get("GMAIL_TOKEN_JSON", "").strip()
    if not raw:
        path = ROOT / "gmail-token.json"
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
    if not raw:
        return None
    return json.loads(raw)


def header_map(message):
    headers = message.get("payload", {}).get("headers", [])
    return {item["name"].lower(): item["value"] for item in headers}


def plain_text(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = plain_text(part)
        if found:
            return found
    return ""


def _decode(data):
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def sender_name(from_header):
    name, address = parseaddr(from_header)
    name = re.sub(r"\s+", " ", name).strip(" \"'")
    if name:
        return name[:80]
    local = address.split("@", 1)[0]
    return local[:80] or "Guest"


def skip_reason(headers):
    sender = headers.get("from", "")
    subject = headers.get("subject", "")
    _, address = parseaddr(sender)
    if address.casefold() == "build@pharmpear.com":
        return "own mailbox"
    if SKIP_FROM.search(address) or SKIP_FROM.search(sender):
        return "automated sender"
    if SKIP_SUBJECT.search(subject.strip()):
        return "automated subject"
    auto = headers.get("auto-submitted", "no").strip().lower()
    if auto and auto != "no":
        return "auto-submitted"
    if headers.get("list-unsubscribe") or headers.get("list-id"):
        return "mailing list"
    precedence = headers.get("precedence", "").lower()
    if precedence in {"bulk", "list", "junk"}:
        return "bulk mail"
    return ""


def _clock(text):
    match = CLOCK.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = match.group(3).replace(".", "").lower()
    if hour > 12 or minute > 59:
        return None
    if ampm.startswith("p") and hour < 12:
        hour += 12
    if ampm.startswith("a") and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return hour, minute


def _day(text, now):
    lowered = text.lower()
    if "tomorrow" in lowered:
        return (now + timedelta(days=1)).date()
    for name, index in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            ahead = (index - now.weekday()) % 7
            if ahead == 0:
                ahead = 7
            return (now + timedelta(days=ahead)).date()
    month = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
        r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        lowered,
    )
    if not month:
        return None
    month_num = MONTHS[month.group(1)]
    day_num = int(month.group(2))
    year = now.year
    try:
        found = datetime(year, month_num, day_num).date()
    except ValueError:
        return None
    if found < now.date():
        try:
            found = datetime(year + 1, month_num, day_num).date()
        except ValueError:
            return None
    return found


def parse_meeting(text, now=None):
    """Return a timezone-aware start when the note asks for a meeting and a time."""
    now = now or datetime.now(TZ)
    if not MEETING.search(text or ""):
        return None
    clock = _clock(text)
    day = _day(text, now)
    if not clock or not day:
        return None
    hour, minute = clock
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    if start <= now:
        return None
    return start


def receipt_text():
    return (
        "We received your message. A person at PharmPear will follow up "
        "from this address.\n\n"
        "Please keep drug names, diagnoses, dates of birth, and member "
        "numbers out of email.\n\n"
        + MARK
    )


def booking_text(start):
    hour = start.strftime("%I").lstrip("0") or "12"
    when = start.strftime(f"%A, %B %d at {hour}:%M %p")
    return (
        f"You are booked for {when} Central Time. "
        "Reply to this email if you need a different time.\n\n"
        + MARK
    )


def _allow_send():
    now = time.time()
    while _sent_times and now - _sent_times[0] > 3600:
        _sent_times.pop(0)
    if len(_sent_times) >= MAX_PER_HOUR:
        return False
    _sent_times.append(now)
    return True


def _watermark():
    STATE.mkdir(exist_ok=True)
    path = STATE / "mail-watermark.txt"
    if path.is_file():
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    stamp = int(time.time() * 1000)
    path.write_text(str(stamp), encoding="utf-8")
    return stamp


def _handled_path():
    STATE.mkdir(exist_ok=True)
    return STATE / "mail-handled.txt"


def _handled_ids():
    path = _handled_path()
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _remember(message_id, seen):
    seen.add(message_id)
    path = _handled_path()
    lines = list(seen)[-500:]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _already_answered(gmail, thread_id):
    thread = gmail.users().threads().get(
        userId="me", id=thread_id, format="metadata", metadataHeaders=["From"]
    ).execute()
    for message in thread.get("messages", []):
        headers = header_map(message)
        snippet = message.get("snippet", "")
        _, address = parseaddr(headers.get("from", ""))
        ours = address.casefold() == "build@pharmpear.com"
        if ours and (
            "We received your message" in snippet or "You are booked for" in snippet
        ):
            return True
    return False


def _send(gmail, message_id, thread_id, headers, text):
    subject = headers.get("subject", "").strip() or "PharmPear"
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    _, address = parseaddr(headers.get("from", ""))
    mime = MIMEText(text, "plain", "utf-8")
    mime["To"] = address
    mime["Subject"] = subject
    mime["Auto-Submitted"] = "auto-replied"
    if headers.get("message-id"):
        mime["In-Reply-To"] = headers["message-id"]
        mime["References"] = headers["message-id"]
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    gmail.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()


def _book(calendar, start, name):
    end = start + timedelta(minutes=30)
    body = {
        "summary": f"Call with {name}",
        "description": "Set from an email to build@pharmpear.com.",
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Chicago"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/Chicago"},
    }
    calendar.events().insert(calendarId="primary", body=body).execute()


def process_once(gmail, calendar, watermark, seen):
    listed = gmail.users().messages().list(
        userId="me",
        q="in:inbox is:unread newer_than:2d",
        maxResults=10,
    ).execute()
    for item in listed.get("messages", []):
        message_id = item["id"]
        if message_id in seen:
            continue
        message = gmail.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        if int(message.get("internalDate", "0")) < watermark:
            _remember(message_id, seen)
            continue
        headers = header_map(message)
        reason = skip_reason(headers)
        if reason:
            log.info("skip %s %s", message_id, reason)
            _remember(message_id, seen)
            continue
        thread_id = message.get("threadId", "")
        if thread_id and _already_answered(gmail, thread_id):
            _remember(message_id, seen)
            continue
        if not _allow_send():
            log.info("hourly reply cap reached")
            return
        body = plain_text(message.get("payload", {}))
        start = parse_meeting(f"{headers.get('subject', '')}\n{body}")
        name = sender_name(headers.get("from", ""))
        if start:
            _book(calendar, start, name)
            text = booking_text(start)
        else:
            text = receipt_text()
        _send(gmail, message_id, thread_id, headers, text)
        _remember(message_id, seen)
        log.info("replied %s booked=%s", message_id, bool(start))


def run():
    logging.basicConfig(level=logging.INFO)
    info = load_token()
    if not info:
        log.warning("mail worker idle: GMAIL_TOKEN_JSON is not set")
        return
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(info)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
    watermark = _watermark()
    seen = _handled_ids()
    log.info("mail worker watching build@pharmpear.com")
    while True:
        try:
            process_once(gmail, calendar, watermark, seen)
        except Exception:
            log.exception("mail poll failed")
        time.sleep(POLL_SECONDS)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=run, name="mail-worker", daemon=True).start()
