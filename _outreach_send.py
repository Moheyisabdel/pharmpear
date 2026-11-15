"""Send one PharmPear vendor request from build@pharmpear.com. Does not print secrets."""
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
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {exc.code} {url} {detail}") from exc

sender = "build@pharmpear.com"
print("FROM", sender)

SIGN = (
    "\n\nPharmPear\n"
    "build@pharmpear.com\n"
    "https://pharmpear.com\n"
    "We are a dispensing pharmacy building our own intake software. "
    "We need a sandbox or API. If a short call is easier, reply with a time and we will confirm it.\n"
)

MESSAGES = [
    ("info@logicoy.com", ["erx.support@logicoy.com"],
     "PharmPear — eRx API sandbox",
     "We are PharmPear, a dispensing pharmacy. Please send sandbox access for your NCPDP SCRIPT eRx API so we can receive electronic prescriptions into our own software. Include pricing and whether EPCS is a separate step."),
    ("info@oystehr.com", ["support@oystehr.com"],
     "PharmPear — eRx service sandbox",
     "We are PharmPear, a dispensing pharmacy. We want sandbox access to the eRx service only, so prescriptions can arrive in our own software. We do not need a full EHR. Please send access steps and pricing, including EPCS."),
    ("info@thepharmacyhub.com", [],
     "PharmPear — pharmacy API for routed orders",
     "We are PharmPear, a dispensing pharmacy. We want API access so prescription orders can be routed to us for filling. Please send onboarding steps and pricing."),
    ("customerservice@mckesson.com", [],
     "PharmPear — new pharmacy wholesale account",
     "We are PharmPear, an independent dispensing pharmacy. Please start a pharmaceutical wholesale account and electronic ordering. If this mailbox is medical-surgical only, please forward this to independent-pharmacy sales."),
    ("Pharma-CustomerService@cardinalhealth.com", [],
     "PharmPear — new pharmacy distribution account",
     "We are PharmPear, an independent dispensing pharmacy. Please start a pharmaceutical distribution account and electronic ordering, and tell us the application link if one is required."),
    ("info@andanet.com", [],
     "PharmPear — new Anda pharmacy account",
     "We are PharmPear, an independent dispensing pharmacy. Please open a secondary wholesale account for generics and short-dated products, and send the electronic-ordering setup."),
    ("sales@morrisdickson.com", ["customerservice@morrisdickson.com"],
     "PharmPear — new pharmacy wholesale account",
     "We are PharmPear, an independent dispensing pharmacy. Please open a wholesale account and send electronic-ordering setup and pricing."),
    ("service@medline.com", ["helpdesk@medline.com"],
     "PharmPear — medical supply account",
     "We are PharmPear, a pharmacy. Please open a medical and surgical supply account and send ordering access."),
    ("medsls@henryschein.com", ["ECS@henryschein.com"],
     "PharmPear — new medical supply account",
     "We are PharmPear, a pharmacy and a new customer. Please open a medical supply account and send online ordering access."),
    ("prescription@centorrx.com", [],
     "PharmPear — prescription vial pricing",
     "We are PharmPear, a dispensing pharmacy. Please send pricing and a sample request path for prescription vials and packaging."),
    ("healthcare@berryglobal.com", [],
     "PharmPear — pharmacy vial pricing",
     "We are PharmPear, a dispensing pharmacy. Please send pricing for prescription vials and pharmacy packaging, and a sample path if you have one."),
    ("customer.service@fagron.us", [],
     "PharmPear — compounding supply account",
     "We are PharmPear. Please start a compounding supply account and send the registration steps. We will provide the state board license and DEA when your form asks for the copies."),
    ("clientcare@pccarx.com", [],
     "PharmPear — compounding account",
     "We are PharmPear, a pharmacy. Please start a compounding account and send the application steps. Our contact for the Houston office is this address."),
    ("medispan-support@wolterskluwer.com", [],
     "PharmPear — Medi-Span API sandbox",
     "We are PharmPear, a single dispensing pharmacy. Our software checks drug interactions before a fill. Please send sandbox or API access and pricing for that use. We are not a software reseller."),
    ("info@drugbank.com", [],
     "PharmPear — clinical API sandbox",
     "We are PharmPear, a dispensing pharmacy. We need the clinical REST API to check drug interactions before a prescription is filled. Please send sandbox access and pricing. The use is our own pharmacy software."),
]

sent = []
for to, cc, subject, body in MESSAGES:
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body + SIGN)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = api("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"raw": raw})
    sent.append({"to": to, "cc": cc, "id": result.get("id"), "subject": subject})
    print("SENT", to, result.get("id"))

(ROOT / "_outreach_sent.json").write_text(json.dumps(sent, indent=2), encoding="utf-8")
print("COUNT", len(sent))
