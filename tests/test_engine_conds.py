"""Transparent .pt cache — naming schema and config flag.

Importing engine pulls in torch (and defensively, chatterbox), so these tests run
where those are installed (i.e. inside the app container), and skip otherwise.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("torch")
engine = pytest.importorskip("engine")


def test_persisted_conds_path_is_model_tagged():
    p = engine.persisted_conds_path("/data/reference_audio/Narrator.wav", "turbo")
    assert p.endswith(os.path.join(".conds", "Narrator.turbo.pt"))
    assert os.path.dirname(p).endswith(os.path.join("reference_audio", ".conds"))


def test_persisted_conds_path_differs_per_model():
    a = engine.persisted_conds_path("/x/Voice.wav", "turbo")
    b = engine.persisted_conds_path("/x/Voice.wav", "original")
    c = engine.persisted_conds_path("/x/Voice.wav", "multilingual")
    assert len({a, b, c}) == 3, "each model must get a distinct .pt filename"


def test_persist_flag_default_true():
    assert engine._persist_conds_enabled() is True
