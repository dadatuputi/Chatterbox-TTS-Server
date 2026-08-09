"""Browser (Playwright) test harness: serve the real app and drive the UI.

Runs a uvicorn server in a thread with the engine mocked and storage in a temp dir,
then drives it with the pre-installed Chromium. Covers UI behaviour (slider
visibility, the create-method tabs, admin-only hiding) — no model/GPU needed.

Run in its own process:  python -m pytest tests/ui -v
"""

import glob
import importlib
import importlib.util
import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest


# ---- stub heavy ML deps when absent (same approach as tests/api) ----
class _Any:
    def __call__(self, *a, **k): return _ANY
    def __getattr__(self, n): return _ANY


_ANY = _Any()


def _absent(mod):
    if mod in sys.modules:
        return False
    try:
        return importlib.util.find_spec(mod) is None
    except (ImportError, ValueError):
        return True


def _perm(m):
    m.__getattr__ = lambda name: _ANY
    return m


def _install_stubs():
    if _absent("torch"):
        t = types.ModuleType("torch")
        t.cuda = types.SimpleNamespace(is_available=lambda: False, is_bf16_supported=lambda: False,
                                       manual_seed=lambda *a: None, manual_seed_all=lambda *a: None,
                                       empty_cache=lambda: None)
        t.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False))
        t.manual_seed = lambda *a: None
        t.Tensor = type("Tensor", (), {})
        sys.modules["torch"] = _perm(t)
    if _absent("torchaudio"):
        sys.modules["torchaudio"] = _perm(types.ModuleType("torchaudio"))
    if _absent("librosa"):
        lib = types.ModuleType("librosa")
        lib.get_duration = lambda path=None, **k: __import__("soundfile").info(str(path)).duration
        sys.modules["librosa"] = lib
    if _absent("chatterbox") and "engine" not in sys.modules:
        eng = types.ModuleType("engine")
        eng.MODEL_LOADED = True
        eng.load_model = lambda: True
        eng.unload_model = lambda: True
        eng.reload_model = lambda: True
        eng.get_model_info = lambda: {"type": "turbo", "class_name": "ChatterboxTurboTTS",
                                      "loaded": True, "supports_paralinguistic_tags": True,
                                      "paralinguistic_tags": []}
        eng.synthesize = lambda *a, **k: (None, 24000)
        sys.modules["engine"] = eng


_install_stubs()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("appdata")
    ref = tmp / "reference_audio"; ref.mkdir()
    voices = tmp / "voices"; voices.mkdir()
    outputs = tmp / "outputs"; outputs.mkdir()

    import config
    config.get_reference_audio_path = lambda ensure_absolute=True: ref
    config.get_predefined_voices_path = lambda ensure_absolute=True: voices
    config.get_output_path = lambda ensure_absolute=True: outputs
    # Never write the repo's config.yaml from a test (the UI persists ui_state on
    # interaction). Keep saves in-memory only.
    config.config_manager.save_config_yaml = lambda: True
    config.config_manager.update_and_save = lambda partial: True

    server = importlib.import_module("server")
    server.get_reference_audio_path = lambda ensure_absolute=True: ref
    server.get_predefined_voices_path = lambda ensure_absolute=True: voices
    server.get_admin_emails = lambda: ["admin@x.com"]
    import engine as eng
    eng.MODEL_LOADED = True
    eng.synthesize = lambda *a, **k: (None, 24000)

    port = _free_port()
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                         log_level="warning", lifespan="off")
    srv = uvicorn.Server(cfg)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(timeout=5)


@pytest.fixture(scope="session")
def _browser():
    from playwright.sync_api import sync_playwright
    chrome = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    pw = sync_playwright().start()
    kw = {"headless": True,
          "args": ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]}
    if chrome:
        kw["executable_path"] = chrome[-1]
    browser = pw.chromium.launch(**kw)
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def open_page(_browser, live_server):
    contexts = []

    def _open(email=None):
        headers = {"Cf-Access-Authenticated-User-Email": email} if email else {}
        ctx = _browser.new_context(base_url=live_server, extra_http_headers=headers)
        try:
            ctx.grant_permissions(["microphone"])
        except Exception:
            pass
        contexts.append(ctx)
        return ctx.new_page()

    yield _open
    for c in contexts:
        c.close()
