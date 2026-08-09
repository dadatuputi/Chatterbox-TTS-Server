"""Real-model generation smoke test.

This is the one piece that needs the actual model + weights + compute, so it is gated:
it only runs when RUN_MODEL_TESTS=1 and torch + chatterbox are importable (i.e. inside
the app container / on the GPU box). It verifies the generation pipeline produces valid,
non-empty audio — it does NOT judge speaker similarity ("does it sound like them"),
which is a subjective quality call.

Enable it:  RUN_MODEL_TESTS=1 python -m pytest tests/model -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_TESTS") != "1",
    reason="set RUN_MODEL_TESTS=1 to run (loads the model + weights)",
)

torch = pytest.importorskip("torch")
pytest.importorskip("chatterbox")
import engine  # noqa: E402


def _length(wav):
    try:
        return int(wav.shape[-1])
    except Exception:
        return len(wav)


@pytest.fixture(scope="module")
def loaded_model():
    assert engine.load_model(), "model failed to load"
    info = engine.get_model_info()
    assert info.get("loaded")
    return info


def _first_voice():
    """A reference wav from the repo's voices/ dir, if any."""
    import glob
    hits = sorted(glob.glob(os.path.join(os.path.dirname(engine.__file__), "voices", "*.wav")))
    return hits[0] if hits else None


def test_generates_valid_audio(loaded_model):
    ref = _first_voice()
    wav, sr = engine.synthesize(
        text="This is a short automated test of speech synthesis.",
        audio_prompt_path=ref, temperature=0.7, exaggeration=0.5, cfg_weight=0.5, seed=1234,
    )
    assert wav is not None and sr == 24000
    # At least ~0.5s of audio for that sentence.
    assert _length(wav) > sr * 0.5


def test_save_to_history_flag(loaded_model, tmp_path, monkeypatch):
    """save_to_history=false must not write to history; =true must (best-of-N enabler)."""
    import importlib
    import history_store as hs
    from starlette.testclient import TestClient

    out = tmp_path / "outputs"; out.mkdir()
    import config
    monkeypatch.setattr(config, "get_output_path", lambda ensure_absolute=True: out, raising=False)
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "get_output_path", lambda ensure_absolute=True: out, raising=False)
    client = TestClient(server.app, raise_server_exceptions=False)

    ref = _first_voice()
    body = {"text": "history flag check.", "voice_mode": "clone" if ref else "predefined",
            "reference_audio_filename": os.path.basename(ref) if ref else None,
            "seed": 7, "save_to_history": False}
    # Trial take: no history.
    client.post("/tts", json={k: v for k, v in body.items() if v is not None},
                headers={"Cf-Access-Authenticated-User-Email": "hist@x.com"})
    assert hs.list_for_user(out, "hist@x.com") == []
    # Kept take: saved.
    body["save_to_history"] = True
    client.post("/tts", json={k: v for k, v in body.items() if v is not None},
                headers={"Cf-Access-Authenticated-User-Email": "hist@x.com"})
    assert len(hs.list_for_user(out, "hist@x.com")) == 1


def test_two_seeds_differ(loaded_model):
    """Sanity: different seeds should not produce byte-identical output."""
    ref = _first_voice()
    kw = dict(text="Seed variation check.", audio_prompt_path=ref,
              temperature=0.8, exaggeration=0.5, cfg_weight=0.5)
    a, _ = engine.synthesize(seed=1, **kw)
    b, _ = engine.synthesize(seed=2, **kw)
    assert a is not None and b is not None
    la, lb = _length(a), _length(b)
    # Either the lengths differ, or the samples differ.
    if la == lb:
        import numpy as np
        aa = a.detach().cpu().numpy().ravel() if hasattr(a, "detach") else np.asarray(a).ravel()
        bb = b.detach().cpu().numpy().ravel() if hasattr(b, "detach") else np.asarray(b).ravel()
        assert not np.array_equal(aa, bb)
