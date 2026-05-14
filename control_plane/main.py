"""
DataWorksAI Control Plane
=========================
Signup portal and dashboard for the hosted agent registry.

Routes
------
GET  /            → landing / signup page
POST /signup      → create tenant, issue API key
GET  /dashboard   → live agent dashboard (requires api_key query param or cookie)
GET  /health      → service health

Environment
-----------
REGISTRY_URL         Internal registry URL   (default: http://registry:6900)
CONTROL_PLANE_SECRET Shared secret for registry /tenants endpoint
PORT                 HTTP port               (default: 8080)
"""

from __future__ import annotations

import os
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32))
CORS(app)

REGISTRY_URL     = os.getenv("REGISTRY_URL", "http://registry:6900").rstrip("/")
CONTROL_SECRET   = os.getenv("CONTROL_PLANE_SECRET", "")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _registry_headers():
    h = {"Content-Type": "application/json"}
    if CONTROL_SECRET:
        h["X-Control-Secret"] = CONTROL_SECRET
    return h


def _get_agents(api_key: str):
    try:
        r = requests.get(
            f"{REGISTRY_URL}/list",
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _get_stats(api_key: str):
    try:
        r = requests.get(
            f"{REGISTRY_URL}/stats",
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template("signup.html")


@app.route("/signup", methods=["POST"])
def signup():
    email = (request.form.get("email") or "").strip()
    if not email:
        return render_template("signup.html", error="Email is required"), 400

    # Derive a TLD from the email domain
    domain = email.split("@")[-1].replace(".", "-") if "@" in email else "user"
    tld    = f"{domain}.agentns.io"

    try:
        r = requests.post(
            f"{REGISTRY_URL}/tenants",
            json={"email": email, "tld": tld},
            headers=_registry_headers(),
            timeout=10,
        )
    except Exception as exc:
        return render_template("signup.html", error=f"Registry unreachable: {exc}"), 503

    if r.status_code == 409:
        return render_template("signup.html", error="Email already registered. Check your inbox."), 409

    if r.status_code not in (200, 201):
        detail = r.json().get("error", r.text)
        return render_template("signup.html", error=f"Signup failed: {detail}"), 500

    data    = r.json()
    api_key = data["api_key"]
    tenant_id = data["tenant_id"]
    issued_tld = data["tld"]

    # Store in session so dashboard can auto-load
    session["api_key"] = api_key

    return render_template(
        "signup.html",
        success=True,
        api_key=api_key,
        tld=issued_tld,
        tenant_id=tenant_id,
    )


@app.route("/dashboard", methods=["GET"])
def dashboard():
    # Accept api_key from query string, session, or cookie
    api_key = (
        request.args.get("api_key")
        or session.get("api_key")
        or request.cookies.get("api_key")
        or ""
    ).strip()

    if not api_key:
        return redirect(url_for("index"))

    agents = _get_agents(api_key)
    stats  = _get_stats(api_key)

    # Fetch tenant info
    tenant_info = {}
    try:
        r = requests.get(
            f"{REGISTRY_URL}/tenants/me",
            headers={"X-API-Key": api_key},
            timeout=5,
        )
        if r.status_code == 200:
            tenant_info = r.json()
    except Exception:
        pass

    resp = make_response(render_template(
        "dashboard.html",
        api_key=api_key,
        agents=agents,
        stats=stats,
        tenant=tenant_info,
        registry_url=REGISTRY_URL,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    ))
    # Keep api_key in cookie for convenience
    resp.set_cookie("api_key", api_key, max_age=86400 * 30, httponly=True, samesite="Lax")
    return resp


@app.route("/health", methods=["GET"])
def health():
    registry_ok = False
    try:
        r = requests.get(f"{REGISTRY_URL}/health", timeout=3)
        registry_ok = r.status_code == 200
    except Exception:
        pass
    return jsonify({
        "status":       "ok",
        "registry":     "ok" if registry_ok else "unreachable",
        "timestamp":    datetime.utcnow().isoformat() + "Z",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🖥️  DataWorksAI Control Plane on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
