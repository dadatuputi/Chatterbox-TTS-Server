"""Phase 4 UI tests: visibility selector, lock indicator, voice annotations."""


def test_visibility_selector_present_and_sent(open_page):
    page = open_page("me@x.com")
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.click("label.voice-mode__option[data-mode='clone']")
    page.wait_for_selector("#voice-visibility", state="visible", timeout=10000)
    # Default is private ("Only me").
    assert page.locator("#voice-visibility").input_value() == "private"

    # Choosing "Everyone" and uploading sends visibility=shared.
    page.select_option("#voice-visibility", "shared")
    sent = {}
    def _capture(route):
        req = route.request
        sent["body"] = req.post_data or ""
        route.fulfill(status=200, content_type="application/json",
                      body='{"uploaded_files":[],"all_reference_files":[],"errors":[]}')
    page.route("**/upload_reference", _capture)
    page.set_input_files("#clone-file-input", files=[{
        "name": "V.wav", "mimeType": "audio/wav", "buffer": b"RIFFxxxxWAVE"}])
    page.wait_for_timeout(500)
    assert "shared" in sent.get("body", "")


def test_admin_sees_unlocked_note(open_page):
    page = open_page("admin@x.com")
    page.goto("/")
    page.wait_for_selector("#server-config-section", state="visible", timeout=10000)
    # Settings is collapsed by default (accordion) — open it to reveal the note.
    page.locator("#server-config-section > summary").click()
    note = page.locator("#settings-unlocked-note")
    note.wait_for(state="visible", timeout=5000)
    assert "Unlocked" in note.inner_text()
    assert "admin@x.com" in note.inner_text()


def test_friend_sees_locked_badge(open_page):
    page = open_page("friend@x.com")
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert page.locator("#settings-locked-badge").is_visible() is True
    assert page.locator("#server-config-section").is_visible() is False
