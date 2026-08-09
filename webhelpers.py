# File: webhelpers.py
# Shared request/identity/history helpers used by both server.py and routes_extra.py.
# Kept out of server.py so our extra endpoints can live in their own module without a
# circular import. Config getters are accessed via the `config` module (config.get_x())
# so tests can monkeypatch them in one place.

import copy
import logging
from pathlib import Path

from fastapi import Request

import config
import voice_import
import history_store

logger = logging.getLogger(__name__)

# Sensitive config keys never sent to a non-admin browser.
SENSITIVE_SERVER_KEYS = ("auth_password", "auth_password_hash", "api_token", "admin_emails")


def current_user(request: Request) -> str:
    """The requesting user's key: the Cloudflare Access email, or 'local' if none."""
    email = request.headers.get("cf-access-authenticated-user-email", "").strip().lower()
    return email or "local"


def is_admin_request(request: Request) -> bool:
    """Whether the current request is from an admin.

    If admin_emails is configured, the Cloudflare Access identity header must match one
    of them (the app is only reachable through the tunnel, so the header is trustworthy).
    Otherwise fall back to app-level auth (Feature B): Basic login is admin, bearer is
    not, auth-off is single-user (admin).
    """
    admin_emails = config.get_admin_emails()
    if admin_emails:
        email = request.headers.get("cf-access-authenticated-user-email", "").strip().lower()
        if email:
            return email in admin_emails
        return getattr(request.state, "auth_method", None) == "basic"
    method = getattr(request.state, "auth_method", None)
    if method == "bearer":
        return False
    return getattr(request.state, "is_admin", True)


def redact_config_for_non_admin(full_config: dict) -> dict:
    """Return a copy of the config with sensitive server keys blanked out."""
    redacted = copy.deepcopy(full_config)
    server = redacted.get("server")
    if isinstance(server, dict):
        for key in SENSITIVE_SERVER_KEYS:
            if key in server:
                server[key] = [] if isinstance(server[key], list) else ""
    return redacted


def enforce_max_duration(path: Path, max_sec, duration: float, warning: str = ""):
    """Trim an over-long reference to the cap instead of rejecting it. Returns
    (possibly updated) duration and the unchanged warning."""
    if max_sec and duration and duration > max_sec:
        try:
            voice_import.trim_audio(str(path), max_sec)
            try:
                import librosa
                duration = float(librosa.get_duration(path=str(path)))
            except Exception:
                duration = float(max_sec)
            logger.info(f"Trimmed reference '{Path(path).name}' to {duration:.1f}s (cap {max_sec}s).")
        except Exception as e:
            logger.warning(f"Could not trim over-long reference '{path}': {e}")
    return duration, warning


def save_to_history(request: Request, audio_bytes: bytes, voice: str, ext: str = "wav") -> None:
    """Best-effort: save a finished generation into the requester's history."""
    try:
        out_root = config.get_output_path(ensure_absolute=True)
        history_store.save_generation(out_root, current_user(request), audio_bytes, voice, ext)
    except Exception as e:
        logger.warning(f"Could not save generation to history: {e}")
