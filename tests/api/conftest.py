"""Integration-test harness for the FastAPI app.

server.py -> engine.py hard-imports chatterbox + torch, which are too heavy for this
sandbox. We stub the heavy modules (only when they're genuinely absent, so a real
in-container run uses the real ones) and drive the app with FastAPI's TestClient
against a temporary reference_audio / voices / outputs tree. Generation is mocked, so
these tests cover routing, auth/identity, form handling, file placement and voice
resolution — not audio synthesis.

Run in its own process:  python -m pytest tests/api -v
"""

import importlib
import importlib.util
import io
import sys
import types
import wave
from pathlib import Path

import pytest


def _absent(mod: str) -> bool:
    if mod in sys.modules:
        return False
    try:
        return importlib.util.find_spec(mod) is None
    except (ImportError, ValueError):
        return True


class _Any:
    """Permissive placeholder usable as a class, callable, or attribute chain."""
    def __call__(self, *a, **k):
        return _ANY

    def __getattr__(self, n):
        return _ANY


_ANY = _Any()


def _permissive(mod: types.ModuleType):
    mod.__getattr__ = lambda name: _ANY  # PEP 562: fires only for missing attrs
    return mod


def _install_stubs():
    if _absent("torch"):
        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(
            is_available=lambda: False, is_bf16_supported=lambda: False,
            manual_seed=lambda *a: None, manual_seed_all=lambda *a: None,
            empty_cache=lambda: None,
        )
        torch.backends = types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)
        )
        torch.manual_seed = lambda *a: None
        torch.Tensor = type("Tensor", (), {})
        sys.modules["torch"] = _permissive(torch)
    if _absent("torchaudio"):
        sys.modules["torchaudio"] = _permissive(types.ModuleType("torchaudio"))
    if _absent("librosa"):
        librosa = types.ModuleType("librosa")

        def _get_duration(path=None, **kw):
            import soundfile as sf
            return float(sf.info(str(path)).duration)

        librosa.get_duration = _get_duration
        sys.modules["librosa"] = librosa
    # Always stub the engine so no model/GPU is needed; real engine requires chatterbox.
    if _absent("chatterbox") and "engine" not in sys.modules:
        engine = types.ModuleType("engine")
        engine.MODEL_LOADED = True  # so endpoints reach voice resolution (synth is mocked)
        engine.load_model = lambda: True
        engine.unload_model = lambda: True
        engine.reload_model = lambda: True
        engine.get_model_info = lambda: {
            "type": "turbo", "class_name": "ChatterboxTurboTTS", "loaded": True,
            "supports_paralinguistic_tags": True, "paralinguistic_tags": [],
        }
        engine.synthesize = lambda *a, **k: (None, 24000)
        sys.modules["engine"] = engine


_install_stubs()


def _write_wav(path: Path, seconds=8.0, sr=24000):
    path.parent.mkdir(parents=True, exist_ok=True)
    import numpy as np
    n = int(sr * seconds)
    data = (0.1 * np.sin(2 * np.pi * 180 * np.arange(n) / sr) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(data.tobytes())
    return path


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Import server with temp storage dirs, return (module, TestClient factory)."""
    ref = tmp_path / "reference_audio"; ref.mkdir()
    voices = tmp_path / "voices"; voices.mkdir()
    outputs = tmp_path / "outputs"; outputs.mkdir()

    import config
    # Point the app's storage at the temp tree.
    monkeypatch.setattr(config, "get_reference_audio_path",
                        lambda ensure_absolute=True: ref, raising=True)
    monkeypatch.setattr(config, "get_predefined_voices_path",
                        lambda ensure_absolute=True: voices, raising=True)
    monkeypatch.setattr(config, "get_output_path",
                        lambda ensure_absolute=True: outputs, raising=True)
    # Never let a test write the repo's config.yaml.
    monkeypatch.setattr(config.config_manager, "save_config_yaml", lambda: True, raising=False)
    monkeypatch.setattr(config.config_manager, "update_and_save", lambda partial: True, raising=False)

    if "server" in sys.modules:
        del sys.modules["server"]
    server = importlib.import_module("server")
    # Rebind the names server imported into its own namespace.
    monkeypatch.setattr(server, "get_reference_audio_path",
                        lambda ensure_absolute=True: ref, raising=False)
    monkeypatch.setattr(server, "get_predefined_voices_path",
                        lambda ensure_absolute=True: voices, raising=False)
    # Make admin identity testable: admin@x.com is admin, everyone else is a "friend".
    monkeypatch.setattr(server, "get_admin_emails", lambda: ["admin@x.com"], raising=False)

    # Mock the engine consistently (works whether engine is the real module in-container
    # or the sandbox stub): "model loaded" so endpoints reach voice resolution, and a
    # synth that returns nothing so no GPU/model is needed.
    import engine as eng
    monkeypatch.setattr(eng, "MODEL_LOADED", True, raising=False)
    monkeypatch.setattr(eng, "synthesize", lambda *a, **k: (None, 24000), raising=False)
    monkeypatch.setattr(eng, "get_model_info", lambda: {
        "type": "turbo", "class_name": "ChatterboxTurboTTS", "loaded": True,
        "supports_paralinguistic_tags": True, "paralinguistic_tags": [],
    }, raising=False)

    from starlette.testclient import TestClient

    def make_client():
        # Surface 500s as responses so tests can assert on status codes cleanly.
        return TestClient(server.app, raise_server_exceptions=False)

    ns = types.SimpleNamespace(
        server=server, ref=ref, voices=voices, outputs=outputs,
        make_client=make_client, write_wav=_write_wav,
    )
    return ns


ADMIN_EMAIL = "admin@x.com"


def hdr(email=None):
    """Build request headers simulating a Cloudflare Access identity."""
    return {"Cf-Access-Authenticated-User-Email": email} if email else {}


def wav_bytes(seconds=8.0, sr=24000):
    """Return valid mono 16-bit WAV bytes (readable by soundfile)."""
    import numpy as np
    n = int(sr * seconds)
    data = (0.1 * np.sin(2 * np.pi * 180 * np.arange(n) / sr) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(data.tobytes())
    return buf.getvalue()
