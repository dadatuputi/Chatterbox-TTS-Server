"""Custom Voices data model: directory-derived ownership/visibility and safe resolution."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voices_store as vs


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF....WAVE")  # content irrelevant for these tests
    return p


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "reference_audio"
    r.mkdir()
    return r


def test_sanitize_user():
    assert vs.sanitize_user("A@B.com") == "a@b.com"
    assert vs.sanitize_user("") == "local"
    assert vs.sanitize_user("../evil/") == ".._evil_"


def test_target_dir_private_vs_shared(root):
    assert vs.target_dir(root, "me@x.com", "private").name == "me@x.com"
    assert vs.target_dir(root, "me@x.com", "private").parent.name == "users"
    assert vs.target_dir(root, "me@x.com", "shared").name == "shared"


def test_listing_scopes_by_user(root):
    _touch(root / "Legacy.wav")                       # legacy flat -> shared
    _touch(root / "shared" / "Team.wav")              # shared
    _touch(root / "users" / "me@x.com" / "Mine.wav")  # my private
    _touch(root / "users" / "you@x.com" / "Yours.wav")  # someone else's private

    mine = {v["filename"] for v in vs.list_voices(root, "me@x.com", is_admin=False)}
    assert mine == {"Legacy.wav", "Team.wav", "Mine.wav"}       # not Yours.wav
    assert "Yours.wav" not in mine

    admin = {v["filename"] for v in vs.list_voices(root, "admin@x.com", is_admin=True)}
    assert admin == {"Legacy.wav", "Team.wav", "Mine.wav", "Yours.wav"}


def test_listing_metadata(root):
    _touch(root / "shared" / "Team.wav")
    _touch(root / "users" / "me@x.com" / "Mine.wav")
    entries = {v["filename"]: v for v in vs.list_voices(root, "me@x.com", is_admin=False)}
    assert entries["Team.wav"]["visibility"] == "all"
    assert entries["Mine.wav"]["visibility"] == "private"
    assert entries["Mine.wav"]["owner"] == "me@x.com"
    assert entries["Team.wav"]["created"]  # ISO timestamp present


def test_resolution_and_access(root):
    _touch(root / "shared" / "Team.wav")
    _touch(root / "users" / "me@x.com" / "Mine.wav")
    _touch(root / "users" / "you@x.com" / "Yours.wav")

    assert vs.resolve(root, "Team.wav", "me@x.com", False).name == "Team.wav"
    assert vs.resolve(root, "Mine.wav", "me@x.com", False) is not None
    # a friend cannot resolve someone else's private voice
    assert vs.resolve(root, "Yours.wav", "me@x.com", False) is None
    # admin can
    assert vs.resolve(root, "Yours.wav", "admin@x.com", True) is not None


def test_resolution_is_traversal_safe(root):
    _touch(root / "shared" / "Team.wav")
    # Any path components are stripped to the basename; cannot escape root.
    assert vs.resolve(root, "../../etc/passwd", "me@x.com", True) is None
    assert vs.resolve(root, "subdir/Team.wav", "me@x.com", False).name == "Team.wav"


def test_own_private_takes_precedence_over_shared(root):
    _touch(root / "shared" / "Dup.wav")
    _touch(root / "users" / "me@x.com" / "Dup.wav")
    entries = [v for v in vs.list_voices(root, "me@x.com", False) if v["filename"] == "Dup.wav"]
    assert len(entries) == 1
    assert entries[0]["visibility"] == "private"  # own copy wins
    # ...and resolution returns the private one
    assert "me@x.com" in str(vs.resolve(root, "Dup.wav", "me@x.com", False))


def test_delete_removes_voice_and_conds(root):
    v = _touch(root / "users" / "me@x.com" / "Mine.wav")
    _touch(root / "users" / "me@x.com" / ".conds" / "Mine.turbo.pt")
    _touch(root / "users" / "me@x.com" / ".conds" / "Mine.original.pt")
    removed = vs.delete_voice(v)
    assert removed == 2
    assert not v.exists()
