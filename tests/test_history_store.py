"""Generation-history data model: save, list, scope, group, disk usage, delete/clear."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_store as hs


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "outputs"
    d.mkdir()
    return d


def test_save_and_list_for_user(out):
    p = hs.save_generation(out, "me@x.com", b"AUDIO", "Narrator.wav", "wav")
    assert p.is_file()
    assert p.parent.name == "me@x.com"
    items = hs.list_for_user(out, "me@x.com")
    assert len(items) == 1
    e = items[0]
    assert e["voice"] == "Narrator" and e["user"] == "me@x.com"
    assert e["size"] == 5 and e["created"]


def test_list_is_per_user(out):
    hs.save_generation(out, "me@x.com", b"a", "V.wav")
    hs.save_generation(out, "you@x.com", b"bb", "W.wav")
    assert len(hs.list_for_user(out, "me@x.com")) == 1
    assert len(hs.list_for_user(out, "you@x.com")) == 1
    assert len(hs.list_all(out)) == 2


def test_newest_first(out):
    a = hs.save_generation(out, "me@x.com", b"a", "V.wav")
    time.sleep(0.02)
    b = hs.save_generation(out, "me@x.com", b"b", "V.wav")
    os.utime(a, (time.time() - 100, time.time() - 100))  # make 'a' older
    items = hs.list_for_user(out, "me@x.com")
    assert items[0]["filename"] == b.name


def test_grouped_for_admin_and_disk_usage(out):
    hs.save_generation(out, "me@x.com", b"1234", "Narrator.wav")
    hs.save_generation(out, "me@x.com", b"12", "Robot.wav")
    hs.save_generation(out, "you@x.com", b"123456", "Narrator.wav")
    g = hs.grouped_for_admin(out)
    assert g["total_size"] == 12
    users = {u["user"]: u for u in g["by_user"]}
    assert users["me@x.com"]["size"] == 6 and len(users["me@x.com"]["items"]) == 2
    assert users["you@x.com"]["size"] == 6
    voices = {v["voice"]: v for v in g["by_voice"]}
    assert voices["Narrator"]["count"] == 2 and voices["Narrator"]["size"] == 10
    assert hs.disk_usage(out) == 12


def test_delete_item_scoped(out):
    p = hs.save_generation(out, "me@x.com", b"a", "V.wav")
    # Wrong user can't resolve/delete it.
    assert hs.resolve(out, "you@x.com", p.name) is None
    assert hs.delete_item(out, "you@x.com", p.name) is False
    assert p.is_file()
    # Owner can.
    assert hs.delete_item(out, "me@x.com", p.name) is True
    assert not p.is_file()


def test_resolve_is_traversal_safe(out):
    hs.save_generation(out, "me@x.com", b"a", "V.wav")
    assert hs.resolve(out, "me@x.com", "../../etc/passwd") is None


def test_clear_user_and_clear_voice(out):
    hs.save_generation(out, "me@x.com", b"a", "Narrator.wav")
    hs.save_generation(out, "me@x.com", b"b", "Robot.wav")
    hs.save_generation(out, "you@x.com", b"c", "Narrator.wav")
    # clear a voice across all users
    assert hs.clear_voice(out, "Narrator") == 2
    assert len(hs.list_all(out)) == 1
    # clear a user
    assert hs.clear_user(out, "me@x.com") == 1
    assert len(hs.list_for_user(out, "me@x.com")) == 0
