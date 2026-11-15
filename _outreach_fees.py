"""Send the fee-service requests that were not sent before. Does not print secrets."""
import base64
import json
from email.message import EmailMessage
from pathlib import Path
from urllib import request, parse, error

ROOT = Path(__file__).resolve().parent
token_path = ROOT / "gmail-token.json"
oauth = json.loads((ROOT / "gmail-oauth.json").read_text(encoding="utf-8"))["installed"]
tok = json.loads(token_path.read_text(encoding="utf-8"))

def refresh():
    data = parse.urlencode({
        "client_id": oauth["client_id"],
        "client_secret": oauth["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = request.Request(oauth["token_uri"], data=data, method="POST")
    with request.urlopen(req, timeout=30) as resp:
        fresh = json.loads(resp.read().decode())
    tok["token"] = fresh["access_token"]
    token_path.write_text(json.dumps(tok), encoding="utf-8")

def api(method, url, body=None, retry=True):
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tok["token"])
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        if exc.code == 401 and retry:
            refresh()
            return api(method, url, body, retry=False)
        detail = exc.read().decode(errors="replace")[:400]
        raise SystemExit(f"HTTP {exc.code} {detail}") from exc

SIGN = (
    "\n\nPlease reply with the fee schedule and two times for a 30-minute call. "
    "The invite should go to build@pharmpear.com.\n\n"
    "PharmPear\nbuild@pharmpear.com\nhttps://pharmpear.com\n"
)
MESSAGES = [
    ("sales@alignrx.org", [],
     "PharmPear — PSAO fees and a call",
     "We are PharmPear, an independent dispensing pharmacy. We need PSAO enrollment so payers can reimburse us. Please send the membership fee, any per-claim fee, and what is included."),
    ("pharmacyhelp@ncpdp.org", ["productinfo@ncpdp.org"],
     "PharmPear — NCPDP pharmacy ID fees",
     "We are PharmPear, a new dispensing pharmacy. Please send the fee to obtain and renew an NCPDP pharmacy ID, and the application steps."),
    ("pharmacy.connections@relayhealth.com", [],
     "PharmPear — pharmacy claims switch fees and a call",
     "We are PharmPear, an independent dispensing pharmacy. We need a claims switch for insurance billing. Please send the per-claim fee, setup fee, and monthly fee."),
    ("sales@cuvo.co", [],
     "PharmPear — dispensing pharmacy, not a telehealth brand",
     "We are PharmPear, a dispensing pharmacy. We want to receive prescription orders from your network. We are not launching a telehealth storefront. Please send the pharmacy-side fees and a time for a call."),
    ("support@srfax.com", [],
     "PharmPear — inbound fax API pricing",
     "We are PharmPear. Our software reads inbound prescription faxes. Please send the monthly number fee, the per-page fee, and HIPAA API access for receiving faxes."),
    ("sales@stripe.com", [],
     "PharmPear — card payment fees for pharmacy orders",
     "We are PharmPear, a dispensing pharmacy. We need card payments tied to each order. Please send the percentage fee, the per-charge fee, and any monthly fee for this use."),
]
sent = []
for to, cc, subject, body in MESSAGES:
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["From"] = "build@pharmpear.com"
    msg["Subject"] = subject
    msg.set_content(body + SIGN)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = api("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"raw": raw})
    sent.append({"to": to, "cc": cc, "id": result.get("id"), "subject": subject})
    print("SENT", to, result.get("id"))
log = ROOT / "_outreach_sent.json"
prior = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
log.write_text(json.dumps(prior + sent, indent=2), encoding="utf-8")
print("COUNT", len(sent))
