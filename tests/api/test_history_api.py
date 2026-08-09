"""Phase 2 integration tests: generation history endpoints over the real app."""

import history_store as hs
from conftest import ADMIN_EMAIL, hdr

ME = "me@x.com"
FRIEND = "friend@x.com"


def _seed(out_root):
    hs.save_generation(out_root, ME, b"aaaa", "Narrator.wav")
    hs.save_generation(out_root, ME, b"bb", "Robot.wav")
    hs.save_generation(out_root, FRIEND, b"cccccc", "Narrator.wav")


def test_history_is_per_user(app_env):
    _seed(app_env.outputs)
    client = app_env.make_client()
    mine = client.get("/api/history", headers=hdr(ME)).json()["items"]
    theirs = client.get("/api/history", headers=hdr(FRIEND)).json()["items"]
    assert {i["voice"] for i in mine} == {"Narrator", "Robot"}
    assert {i["voice"] for i in theirs} == {"Narrator"}
    assert all(i["user"] == ME for i in mine)


def test_admin_grouped_view_and_disk(app_env):
    _seed(app_env.outputs)
    client = app_env.make_client()
    # Non-admin is refused the admin view.
    assert client.get("/api/history/admin", headers=hdr(ME)).status_code == 403
    g = client.get("/api/history/admin", headers=hdr(ADMIN_EMAIL)).json()
    assert g["total_size"] == 12
    users = {u["user"]: u for u in g["by_user"]}
    assert users[ME]["size"] == 6 and users[FRIEND]["size"] == 6
    voices = {v["voice"]: v for v in g["by_voice"]}
    assert voices["Narrator"]["count"] == 2


def test_download_scoped(app_env):
    _seed(app_env.outputs)
    client = app_env.make_client()
    name = client.get("/api/history", headers=hdr(ME)).json()["items"][0]["filename"]
    # Owner downloads their own item.
    r = client.get("/history/file", params={"name": name}, headers=hdr(ME))
    assert r.status_code == 200 and r.content
    # A friend cannot fetch someone else's item (their dir doesn't have it).
    r = client.get("/history/file", params={"name": name}, headers=hdr(FRIEND))
    assert r.status_code == 404
    # Admin can fetch anyone's by naming the user.
    r = client.get("/history/file", params={"name": name, "user": ME}, headers=hdr(ADMIN_EMAIL))
    assert r.status_code == 200


def test_delete_scoped(app_env):
    _seed(app_env.outputs)
    client = app_env.make_client()
    name = client.get("/api/history", headers=hdr(ME)).json()["items"][0]["filename"]
    # Friend can't delete my item by targeting my user.
    r = client.post("/history/delete", data={"name": name, "user": ME}, headers=hdr(FRIEND))
    assert r.status_code == 403
    # Owner deletes their own.
    r = client.post("/history/delete", data={"name": name}, headers=hdr(ME))
    assert r.status_code == 200
    assert name not in {i["filename"] for i in client.get("/api/history", headers=hdr(ME)).json()["items"]}


def test_tts_accepts_save_to_history_flag(app_env):
    """The best-of-N flag is accepted by /tts (not a 422); real effect is model-gated."""
    client = app_env.make_client()
    _upload = client.post
    # Need a resolvable shared voice so we get past resolution to the (mocked) engine.
    import voices_store as vs
    (app_env.ref / "shared").mkdir(exist_ok=True)
    (app_env.ref / "shared" / "V.wav").write_bytes(b"RIFFxxxxWAVE")
    r = client.post("/tts", json={
        "text": "hi", "voice_mode": "clone", "reference_audio_filename": "V.wav",
        "seed": 3, "save_to_history": False,
    }, headers=hdr(ME))
    assert r.status_code != 422, r.text  # field accepted (fails later at mocked synth)


def test_clear_group_permissions(app_env):
    _seed(app_env.outputs)
    client = app_env.make_client()
    # Non-admin cannot clear a voice group.
    assert client.post("/history/clear", data={"scope": "voice", "key": "Narrator"},
                       headers=hdr(ME)).status_code == 403
    # Admin clears the Narrator voice across all users.
    r = client.post("/history/clear", data={"scope": "voice", "key": "Narrator"},
                    headers=hdr(ADMIN_EMAIL))
    assert r.status_code == 200 and r.json()["removed"] == 2
    # A user can clear their own history.
    r = client.post("/history/clear", data={"scope": "user"}, headers=hdr(ME))
    assert r.status_code == 200
    assert client.get("/api/history", headers=hdr(ME)).json()["items"] == []
