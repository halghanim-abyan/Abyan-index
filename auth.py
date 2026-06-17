"""
auth.py — lightweight login gate for the internal team (Streamlit-native).

Credentials live in st.secrets (encrypted on Streamlit Cloud, never in the
repo), so no extra auth library / version churn. Behaviour by environment:

  • Local dev (no secrets file)         → NO login (open), so your local
    dashboard keeps working exactly as before.
  • Deployed WITH an [auth] section     → login required.
  • Deployed but [auth] MISSING         → fail CLOSED (blocked) with a clear
    message, so a misconfigured deploy can never expose data publicly.

secrets.toml format:
    [auth]
    cookie_key = "any-random-string"   # optional, reserved
    [auth.users]
    hadi  = "the-password"
    sara  = "another-password"
"""

from __future__ import annotations

import hmac

import streamlit as st


def _auth_config():
    """Return the [auth] mapping, None (local/open), or 'MISCONFIGURED'."""
    try:
        has_any = len(st.secrets) > 0
    except Exception:
        return None  # no secrets.toml at all → local dev → open
    try:
        if "auth" in st.secrets:
            return st.secrets["auth"]
    except Exception:
        return None
    # Secrets exist (deployed) but no [auth] section → refuse to run open.
    return "MISCONFIGURED" if has_any else None


def require_login() -> None:
    """Block the app until the user authenticates (no-op in open local dev)."""
    cfg = _auth_config()
    if cfg is None:
        return  # local dev — open

    if cfg == "MISCONFIGURED":
        st.error(
            "🔒 لم تُضبط المصادقة. أضِف قسم [auth] في إعدادات Secrets قبل النشر."
        )
        st.stop()

    if st.session_state.get("_authed"):
        with st.sidebar:
            st.caption(f"👤 {st.session_state.get('_user', '')}")
            if st.button("تسجيل الخروج / Logout"):
                st.session_state.clear()
                st.rerun()
        return

    # ── Login form ──────────────────────────────────────────────────────
    st.markdown("### 🔒 Abyan Terminal — تسجيل الدخول")
    with st.form("login_form"):
        username = st.text_input("اسم المستخدم / Username")
        password = st.text_input("كلمة المرور / Password", type="password")
        submitted = st.form_submit_button("دخول / Login")

    if submitted:
        users = {}
        try:
            users = dict(cfg.get("users", {}))
        except Exception:
            users = {}
        stored = users.get(username)
        # constant-time compare; reject if user unknown
        ok = stored is not None and hmac.compare_digest(str(stored), str(password))
        if ok:
            st.session_state["_authed"] = True
            st.session_state["_user"] = username
            st.rerun()
        else:
            st.error("بيانات الدخول غير صحيحة / Invalid credentials")

    st.stop()
