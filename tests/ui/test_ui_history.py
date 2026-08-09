"""Phase 4b UI tests: the generation-history panel (user + admin views)."""

import json


def _route_history(page, items):
    page.route("**/api/history", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"items": items})))


def test_history_list_renders(open_page):
    page = open_page("me@x.com")
    _route_history(page, [
        {"filename": "a.wav", "user": "me@x.com", "voice": "Narrator",
         "created": "2026-08-08T10:00:00Z", "created_ts": 1, "size": 100},
        {"filename": "b.wav", "user": "me@x.com", "voice": "Robot",
         "created": "2026-08-08T09:00:00Z", "created_ts": 0, "size": 50},
    ])
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#history-list .history-row", timeout=10000)
    rows = page.locator("#history-list .history-row")
    assert rows.count() == 2
    assert "Narrator" in page.locator("#history-list").inner_text()


def test_history_empty_message(open_page):
    page = open_page("me@x.com")
    _route_history(page, [])
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator("#history-empty").is_visible() is True


def test_admin_grouped_history_renders(open_page):
    page = open_page("admin@x.com")
    _route_history(page, [])
    page.route("**/api/history/admin", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({
            "total_size": 1536,
            "by_user": [{"user": "me@x.com", "size": 1024, "items": [{}, {}]},
                        {"user": "you@x.com", "size": 512, "items": [{}]}],
            "by_voice": [{"voice": "Narrator", "size": 1024, "count": 2}],
        })))
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#history-by-user .history-row", timeout=10000)
    assert page.locator("#history-by-user .history-row").count() == 2
    assert page.locator("#history-by-voice .history-row").count() == 1
    assert "1.5 KB" in page.locator("#history-admin-total").inner_text()


def test_admin_history_hidden_for_friend(open_page):
    page = open_page("friend@x.com")
    _route_history(page, [])
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator("#history-admin").is_visible() is False
