# File: auth.py
# HTTP Basic authentication + optional bearer-token middleware for the TTS server.
#
# Feature B (see BUILD_SPEC): off by default, enabled via config.yaml following the
# ssl_certfile / ssl_keyfile pattern. Middleware covers ALL routes (fail closed) so
# a route added later is protected without anyone remembering to decorate it.
#
# Two credential paths are supported simultaneously:
#   * HTTP Basic  -> browser / UI access. A valid Basic user is treated as "admin".
#   * Bearer token -> the /v1/audio/* API surface (Open WebUI, scripts). Non-admin.
#
# Passwords are compared in constant time. A bcrypt hash (auth_password_hash) is
# preferred; a plaintext password (auth_password) is accepted as a fallback so the
# feature works without the optional bcrypt dependency installed.

import base64
import binascii
import logging
import secrets
import time
from typing import Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Optional dependency. If bcrypt is unavailable we fall back to plaintext comparison.
try:  # pragma: no cover - exercised via runtime import
    import bcrypt as _bcrypt

    _BCRYPT_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure means "not available"
    _bcrypt = None
    _BCRYPT_AVAILABLE = False


# --- Password hashing helpers -------------------------------------------------

def bcrypt_available() -> bool:
    """True when the optional bcrypt dependency is importable."""
    return _BCRYPT_AVAILABLE


def hash_password(password: str) -> str:
    """Return a bcrypt hash string for the given password.

    Raises RuntimeError if bcrypt is not installed.
    """
    if not _BCRYPT_AVAILABLE:
        raise RuntimeError(
            "bcrypt is not installed. Install it (pip install bcrypt) to generate a "
            "password hash, or use the plaintext 'auth_password' config field instead."
        )
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, *, password_hash: str = "", plaintext: str = "") -> bool:
    """Constant-time verification of a password against a bcrypt hash or plaintext.

    The bcrypt hash takes precedence when present. Returns False when neither a hash
    nor a plaintext reference is configured.
    """
    if password_hash:
        if not _BCRYPT_AVAILABLE:
            logger.error(
                "auth_password_hash is set but bcrypt is not installed; cannot verify. "
                "Install bcrypt or switch to the plaintext auth_password field."
            )
            return False
        try:
            return _bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except (ValueError, TypeError) as e:
            logger.error(f"Malformed auth_password_hash in config: {e}")
            return False
    if plaintext:
        return secrets.compare_digest(password.encode("utf-8"), plaintext.encode("utf-8"))
    return False


def _parse_basic(header_value: str) -> Optional[Tuple[str, str]]:
    """Parse a 'Basic base64(user:pass)' header into (user, pass) or None."""
    try:
        scheme, _, encoded = header_value.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return None
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        username, sep, password = decoded.partition(":")
        if not sep:
            return None
        return username, password
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _parse_bearer(header_value: str) -> Optional[str]:
    """Parse a 'Bearer <token>' header into the token or None."""
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


# --- Simple in-memory rate limiter / lockout ---------------------------------

class _RateLimiter:
    """Per-IP failed-attempt tracker with a temporary lockout.

    Deliberately in-process and lightweight — for a single-user box behind TLS this
    blunts online brute force. A real deployment should still front this with a proxy
    / fail2ban (see BUILD_SPEC section 3).
    """

    def __init__(self, max_failures: int = 8, window_sec: float = 300.0, lockout_sec: float = 300.0):
        self.max_failures = max_failures
        self.window_sec = window_sec
        self.lockout_sec = lockout_sec
        # ip -> (failure_count, first_failure_ts, locked_until_ts)
        self._state: Dict[str, Tuple[int, float, float]] = {}

    def is_locked(self, ip: str) -> Tuple[bool, float]:
        now = time.monotonic()
        count, first_ts, locked_until = self._state.get(ip, (0, 0.0, 0.0))
        if locked_until and now < locked_until:
            return True, locked_until - now
        return False, 0.0

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        count, first_ts, locked_until = self._state.get(ip, (0, now, 0.0))
        # Reset the window if it has elapsed since the first failure.
        if now - first_ts > self.window_sec:
            count, first_ts = 0, now
        count += 1
        if count >= self.max_failures:
            locked_until = now + self.lockout_sec
            logger.warning(
                f"Auth: locking out {ip} for {self.lockout_sec:.0f}s after {count} failed attempts."
            )
        self._state[ip] = (count, first_ts, locked_until)

    def record_success(self, ip: str) -> None:
        self._state.pop(ip, None)


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces HTTP Basic (and optional bearer-token) auth across all routes.

    Sets ``request.state.is_admin`` for downstream handlers:
        * True  when auth is disabled (single-user assumption) or a valid Basic login.
        * False when authenticated via bearer token (API surface).
    """

    def __init__(self, app, config_provider):
        super().__init__(app)
        # config_provider() returns the current auth config dict (re-read each request
        # so a saved config change takes effect without a process restart).
        self._config_provider = config_provider
        self._limiter = _RateLimiter()

    def _client_ip(self, request: Request) -> str:
        # Honour a single upstream proxy hop if present; otherwise the socket peer.
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        cfg = self._config_provider()

        if not cfg.get("enabled"):
            request.state.is_admin = True
            request.state.auth_method = "none"
            return await call_next(request)

        # Let CORS preflight through — it never carries credentials.
        if request.method == "OPTIONS":
            return await call_next(request)

        ip = self._client_ip(request)
        locked, retry_after = self._limiter.is_locked(ip)
        if locked:
            return JSONResponse(
                {"detail": "Too many failed attempts. Try again later."},
                status_code=429,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        auth_header = request.headers.get("authorization", "")
        is_admin = self._check_basic(auth_header, cfg)
        auth_method = "basic" if is_admin else None

        if not is_admin and not auth_method:
            if self._check_bearer(auth_header, cfg):
                auth_method = "bearer"

        if auth_method is None:
            self._limiter.record_failure(ip)
            return self._challenge()

        self._limiter.record_success(ip)
        request.state.is_admin = is_admin
        request.state.auth_method = auth_method
        return await call_next(request)

    def _check_basic(self, auth_header: str, cfg: dict) -> bool:
        parsed = _parse_basic(auth_header)
        if not parsed:
            return False
        username, password = parsed
        expected_user = cfg.get("username", "")
        # Constant-time username compare, then password verification.
        user_ok = secrets.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
        pass_ok = verify_password(
            password,
            password_hash=cfg.get("password_hash", ""),
            plaintext=cfg.get("password", ""),
        )
        # Evaluate both regardless of the username result to avoid short-circuit timing leaks.
        return user_ok and pass_ok

    def _check_bearer(self, auth_header: str, cfg: dict) -> bool:
        token = _parse_bearer(auth_header)
        api_token = cfg.get("api_token", "")
        if not token or not api_token:
            return False
        return secrets.compare_digest(token.encode("utf-8"), api_token.encode("utf-8"))

    def _challenge(self) -> Response:
        return JSONResponse(
            {"detail": "Authentication required."},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Chatterbox TTS Server"'},
        )
