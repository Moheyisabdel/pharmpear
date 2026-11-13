"""PharmPear site with email login and a locked session."""

import hashlib
import hmac
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, redirect, request, send_from_directory, session

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
IDLE_SECONDS = 30 * 60
MAX_SESSION_SECONDS = 8 * 60 * 60
LOCK_SECONDS = 15 * 60
MAX_FAILS = 8
_fails = {}


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=MAX_SESSION_SECONDS),
)

PUBLIC_FILES = {"styles.css", "script.js", "favicon.svg", "robots.txt"}


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def locked(ip):
    row = _fails.get(ip)
    if not row:
        return False
    if time.time() - row["start"] > LOCK_SECONDS:
        _fails.pop(ip, None)
        return False
    return row["count"] >= MAX_FAILS


def note_failure(ip):
    now = time.time()
    row = _fails.get(ip)
    if not row or now - row["start"] > LOCK_SECONDS:
        _fails[ip] = {"count": 1, "start": now}
        return
    row["count"] += 1


def clear_failures(ip):
    _fails.pop(ip, None)


def password_ok(given):
    stored = os.environ.get("ADMIN_PASSWORD_HASH", "")
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    _, rounds, salt, expected = parts
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", given.encode("utf-8"), salt.encode("utf-8"), int(rounds)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def email_ok(given):
    expected = os.environ.get("ADMIN_EMAIL", "").strip().casefold()
    if not expected:
        return False
    left = hashlib.sha256(given.strip().casefold().encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def session_ok():
    if not session.get("user"):
        return False
    now = time.time()
    started = session.get("started") or 0
    seen = session.get("seen") or 0
    if now - started > MAX_SESSION_SECONDS or now - seen > IDLE_SECONDS:
        session.clear()
        return False
    session["seen"] = now
    return True


def secure_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self' mailto:; "
        "frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


app.after_request(secure_headers)


def render_login(error=""):
    html = (PUBLIC / "login.html").read_text(encoding="utf-8")
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    html = html.replace("__CSRF__", token).replace("__ERROR__", error)
    return html


@app.get("/login")
def login_page():
    if session_ok():
        return redirect("/")
    return render_login()


@app.post("/login")
def login_post():
    ip = client_ip()
    if locked(ip):
        return render_login("Too many tries. Wait 15 minutes."), 429
    token = session.get("csrf") or ""
    sent = request.form.get("csrf") or ""
    if not token or not hmac.compare_digest(token, sent):
        session["csrf"] = secrets.token_urlsafe(32)
        return render_login("Sign-in expired. Try again."), 400
    email = request.form.get("email") or ""
    password = request.form.get("password") or ""
    ok_email = email_ok(email)
    ok_password = password_ok(password)
    if ok_email and ok_password:
        clear_failures(ip)
        now = time.time()
        session.clear()
        session.permanent = True
        session["user"] = os.environ["ADMIN_EMAIL"].strip()
        session["started"] = now
        session["seen"] = now
        session["csrf"] = secrets.token_urlsafe(32)
        return redirect("/")
    note_failure(ip)
    session["csrf"] = secrets.token_urlsafe(32)
    if locked(ip):
        return render_login("Too many tries. Wait 15 minutes."), 429
    return render_login("That email or password is wrong."), 401


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/robots.txt")
def robots():
    return send_from_directory(PUBLIC, "robots.txt")


@app.get("/")
def home():
    return send_page("index.html")


@app.get("/privacy")
@app.get("/privacy.html")
def privacy():
    return send_page("privacy.html")


def send_page(name):
    html = (PUBLIC / name).read_text(encoding="utf-8")
    user = session.get("user") if session_ok() else ""
    if user:
        bar = f'<p class="secure-bar">Signed in as {user}</p>'
        auth = '<a class="logout" href="/logout">Log out</a>'
    else:
        bar = ""
        auth = '<a class="logout" href="/login">Sign in</a>'
    return html.replace("__BAR__", bar).replace("__AUTH__", auth)


@app.get("/<path:name>")
def asset(name):
    if name not in PUBLIC_FILES:
        abort(404)
    return send_from_directory(PUBLIC, name)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8780")), debug=False)
