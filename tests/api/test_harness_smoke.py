"""Smoke test: the app imports under the stub harness and serves basic requests."""


def test_app_imports_and_serves(app_env):
    client = app_env.make_client()
    r = client.get("/api/model-info")
    assert r.status_code == 200

    r = client.get("/get_reference_files")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_initial_data_shape(app_env):
    client = app_env.make_client()
    r = client.get("/api/ui/initial-data")
    assert r.status_code == 200
    data = r.json()
    assert "config" in data and "capabilities" in data
    assert "is_admin" in data["capabilities"]
