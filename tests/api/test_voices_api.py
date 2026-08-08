"""Phase 1 integration tests: per-user voice ownership/visibility over the real endpoints."""

from conftest import ADMIN_EMAIL, hdr, wav_bytes

ME = "me@x.com"
FRIEND = "friend@x.com"


def _upload(client, name, email, visibility):
    return client.post(
        "/upload_reference",
        files={"files": (name, wav_bytes(), "audio/wav")},
        data={"visibility": visibility},
        headers=hdr(email),
    )


def test_private_upload_lands_in_user_dir(app_env):
    client = app_env.make_client()
    r = _upload(client, "Mine.wav", ME, "private")
    assert r.status_code == 200, r.text
    assert (app_env.ref / "users" / ME / "Mine.wav").is_file()


def test_shared_upload_lands_in_shared_dir(app_env):
    client = app_env.make_client()
    r = _upload(client, "Team.wav", ME, "shared")
    assert r.status_code == 200, r.text
    assert (app_env.ref / "shared" / "Team.wav").is_file()


def test_listing_is_scoped_by_user(app_env):
    client = app_env.make_client()
    _upload(client, "Mine.wav", ME, "private")
    _upload(client, "Team.wav", ME, "shared")
    _upload(client, "Yours.wav", FRIEND, "private")

    def names(email):
        r = client.get("/get_reference_files", headers=hdr(email))
        assert r.status_code == 200
        return set(r.json())

    assert names(ME) == {"Mine.wav", "Team.wav"}          # not Yours.wav
    assert names(FRIEND) == {"Yours.wav", "Team.wav"}     # not Mine.wav
    assert names(ADMIN_EMAIL) == {"Mine.wav", "Team.wav", "Yours.wav"}  # admin sees all


def test_api_voices_metadata(app_env):
    client = app_env.make_client()
    _upload(client, "Mine.wav", ME, "private")
    _upload(client, "Team.wav", ME, "shared")
    r = client.get("/api/voices", headers=hdr(ME))
    assert r.status_code == 200
    by = {v["filename"]: v for v in r.json()}
    assert by["Mine.wav"]["visibility"] == "private" and by["Mine.wav"]["owner"] == ME
    assert by["Team.wav"]["visibility"] == "all"
    assert by["Mine.wav"]["created"]


def test_delete_permissions(app_env):
    client = app_env.make_client()
    _upload(client, "Mine.wav", ME, "private")
    _upload(client, "Team.wav", ME, "shared")

    # A friend cannot delete a shared voice.
    r = client.post("/delete_reference", data={"name": "Team.wav"}, headers=hdr(FRIEND))
    assert r.status_code == 403, r.text
    # The owner can delete their own private voice.
    r = client.post("/delete_reference", data={"name": "Mine.wav"}, headers=hdr(ME))
    assert r.status_code == 200, r.text
    assert not (app_env.ref / "users" / ME / "Mine.wav").exists()
    # Admin can delete the shared voice.
    r = client.post("/delete_reference", data={"name": "Team.wav"}, headers=hdr(ADMIN_EMAIL))
    assert r.status_code == 200, r.text


def test_download_returns_zip(app_env):
    client = app_env.make_client()
    _upload(client, "Team.wav", ME, "shared")
    r = client.get("/download_voice", params={"name": "Team.wav"}, headers=hdr(ME))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"  # zip magic


def test_tts_clone_resolution_is_scoped(app_env):
    """Owner passes resolution (fails later at the stubbed synth); a friend gets 404."""
    client = app_env.make_client()
    _upload(client, "Mine.wav", ME, "private")

    body = {"text": "hello", "voice_mode": "clone", "reference_audio_filename": "Mine.wav"}
    # Friend can't resolve someone else's private voice -> 404.
    r = client.post("/tts", json=body, headers=hdr(FRIEND))
    assert r.status_code == 404, r.text
    # Owner resolves it (then errors at the mocked engine, i.e. NOT a 404).
    r = client.post("/tts", json=body, headers=hdr(ME))
    assert r.status_code != 404, r.text


def test_v1_ignores_private_voices(app_env):
    client = app_env.make_client()
    _upload(client, "Mine.wav", ME, "private")
    _upload(client, "Team.wav", ME, "shared")
    # Private voice is not reachable via the API surface.
    r = client.post("/v1/audio/speech",
                    json={"model": "chatterbox", "input": "hi", "voice": "Mine.wav"})
    assert r.status_code == 404, r.text
    # Shared voice resolves (then hits the model-not-loaded / stub path, not a 404).
    r = client.post("/v1/audio/speech",
                    json={"model": "chatterbox", "input": "hi", "voice": "Team.wav"})
    assert r.status_code != 404, r.text
