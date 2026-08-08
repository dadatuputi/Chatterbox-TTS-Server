"""Feature B — HTTP auth middleware: fail-closed, basic + bearer, lockout."""

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("starlette")
pytest.importorskip("bcrypt")

import auth
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _basic(user, pw):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client_and_cfg():
    cfg = {"enabled": False, "username": "admin", "password": "",
           "password_hash": "", "api_token": ""}

    async def whoami(request):
        return JSONResponse({
            "is_admin": getattr(request.state, "is_admin", None),
            "method": getattr(request.state, "auth_method", None),
        })

    app = Starlette(
        routes=[Route("/whoami", whoami, methods=["GET", "POST", "OPTIONS"])],
        middleware=[Middleware(auth.AuthMiddleware, config_provider=lambda: cfg)],
    )
    return TestClient(app), cfg


def test_hash_and_verify_roundtrip():
    h = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", password_hash=h) is True
    assert auth.verify_password("wrong", password_hash=h) is False


def test_plaintext_verify():
    assert auth.verify_password("pw", plaintext="pw") is True
    assert auth.verify_password("pw", plaintext="other") is False
    assert auth.verify_password("anything") is False  # nothing configured


def test_disabled_is_open_and_admin(client_and_cfg):
    client, cfg = client_and_cfg
    r = client.get("/whoami")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_enabled_requires_credentials(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password_hash=auth.hash_password("hunter2"), api_token="tok")
    r = client.get("/whoami")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("basic")


def test_good_basic_is_admin(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password_hash=auth.hash_password("hunter2"))
    r = client.get("/whoami", headers=_basic("admin", "hunter2"))
    assert r.status_code == 200
    assert r.json() == {"is_admin": True, "method": "basic"}


def test_bad_password_and_user_rejected(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password_hash=auth.hash_password("hunter2"))
    assert client.get("/whoami", headers=_basic("admin", "nope")).status_code == 401
    assert client.get("/whoami", headers=_basic("root", "hunter2")).status_code == 401


def test_bearer_token_is_non_admin(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password="pw", api_token="tok_secret")
    r = client.get("/whoami", headers={"Authorization": "Bearer tok_secret"})
    assert r.status_code == 200
    assert r.json() == {"is_admin": False, "method": "bearer"}
    assert client.get("/whoami", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_options_passthrough(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password="pw")
    # OPTIONS (CORS preflight) is allowed through without credentials.
    assert client.options("/whoami").status_code in (200, 405)


def test_lockout_after_repeated_failures(client_and_cfg):
    client, cfg = client_and_cfg
    cfg.update(enabled=True, password="pw")
    codes = [client.get("/whoami", headers=_basic("admin", "x")).status_code for _ in range(9)]
    final = client.get("/whoami", headers=_basic("admin", "pw")).status_code
    assert 429 in codes or final == 429
