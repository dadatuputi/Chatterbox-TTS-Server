"""Browser UI regressions driven against the real app."""


def test_exaggeration_slider_visible(open_page):
    """Regression: Exaggeration was hidden on Turbo; it must be visible on all models."""
    page = open_page("admin@x.com")
    page.goto("/")
    page.wait_for_selector("#exaggeration-group", state="visible", timeout=15000)


def test_create_method_tabs_switch(open_page):
    """The three distinct create methods (Upload / URL / Record) switch panels."""
    page = open_page("me@x.com")
    page.goto("/")
    page.wait_for_load_state("networkidle")
    # Enter Custom Voices (clone) mode.
    page.click("label.voice-mode__option[data-mode='clone']")
    page.wait_for_selector("#clone-options", state="visible", timeout=10000)
    # Upload panel is the default; switch to URL then Record.
    page.click("#voice-tab-url")
    assert page.locator("#url-import-panel").is_visible()
    page.click("#voice-tab-record")
    assert page.locator("#mic-record-panel").is_visible()
    assert not page.locator("#url-import-panel").is_visible()


def test_settings_hidden_for_friend(open_page):
    page = open_page("friend@x.com")  # not in admin_emails
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)  # let applyCapabilities run
    assert page.locator("#server-config-section").is_visible() is False


def test_settings_visible_for_admin(open_page):
    page = open_page("admin@x.com")
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#server-config-section", state="visible", timeout=10000)
