"""Transparent .pt conditioning cache (engine_ext) — now torch-free and fully tested."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine_ext as ex


class FakeConds:
    """Stand-in for chatterbox's Conditionals: .save writes a tag, .load reads it back."""
    def __init__(self, tag):
        self.tag = tag

    def save(self, path):
        with open(path, "w") as f:
            f.write(self.tag)

    @classmethod
    def load(cls, path, map_location=None):
        with open(path) as f:
            return cls(f.read())


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    # Avoid importing config/torch; force persistence on unless a test overrides.
    monkeypatch.setattr(ex, "persist_enabled", lambda: True)


def test_path_is_model_tagged_and_distinct():
    p = ex.persisted_conds_path("/data/reference_audio/Narrator.wav", "turbo")
    assert p.endswith(os.path.join(".conds", "Narrator.turbo.pt"))
    a = ex.persisted_conds_path("/x/V.wav", "turbo")
    b = ex.persisted_conds_path("/x/V.wav", "original")
    c = ex.persisted_conds_path("/x/V.wav", "multilingual")
    assert len({a, b, c}) == 3


def test_save_then_load_roundtrip(tmp_path):
    audio = tmp_path / "V.wav"; audio.write_bytes(b"audio")
    ex.try_save_conds(str(audio), "turbo", FakeConds("TURBO-CONDS"))
    pt = tmp_path / ".conds" / "V.turbo.pt"
    assert pt.is_file()
    loaded = ex.try_load_conds(str(audio), "turbo", FakeConds, None)
    assert loaded is not None and loaded.tag == "TURBO-CONDS"


def test_load_returns_none_for_wrong_model(tmp_path):
    audio = tmp_path / "V.wav"; audio.write_bytes(b"audio")
    ex.try_save_conds(str(audio), "turbo", FakeConds("X"))
    # A different model has no matching .pt.
    assert ex.try_load_conds(str(audio), "original", FakeConds, None) is None


def test_load_returns_none_when_stale(tmp_path):
    audio = tmp_path / "V.wav"; audio.write_bytes(b"audio")
    ex.try_save_conds(str(audio), "turbo", FakeConds("X"))
    # Source audio replaced *after* baking -> cache is stale, ignored.
    time.sleep(0.01)
    os.utime(audio, None)  # bump mtime to now
    assert ex.try_load_conds(str(audio), "turbo", FakeConds, None) is None


def test_save_is_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "persist_enabled", lambda: False)
    audio = tmp_path / "V.wav"; audio.write_bytes(b"audio")
    ex.try_save_conds(str(audio), "turbo", FakeConds("X"))
    assert not (tmp_path / ".conds").exists()
