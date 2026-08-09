"""Phase 4d UI tests: presets under the text box (collapsed) + accordion behavior."""


def test_presets_are_under_text_and_collapsed(open_page):
    page = open_page("admin@x.com")
    page.goto("/")
    page.wait_for_load_state("networkidle")
    # The presets collapsible exists and starts closed (collapsed by default).
    coll = page.locator("#presets-collapsible")
    assert coll.count() == 1
    assert coll.evaluate("el => el.open") is False
    # It sits after the text textarea in document order.
    order = page.evaluate(
        "() => { const t=document.getElementById('text'); const p=document.getElementById('presets-collapsible');"
        " return t.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING ? 'after' : 'before'; }"
    )
    assert order == "after"
    # Opening it reveals the preset buttons container.
    coll.locator("summary").click()
    page.wait_for_selector("#presets-container", state="visible", timeout=5000)


def test_settings_accordion_one_open(open_page):
    page = open_page("admin@x.com")
    page.goto("/")
    page.wait_for_selector("#server-config-section", state="visible", timeout=10000)
    gen = page.locator("details.js-accordion[data-accordion='settings']").first
    cfg = page.locator("#server-config-section")
    # Generation Parameters starts open, Server Config closed.
    assert gen.evaluate("el => el.open") is True
    assert cfg.evaluate("el => el.open") is False
    # Opening Server Config closes Generation Parameters (accordion).
    cfg.locator("summary").click()
    page.wait_for_timeout(300)
    assert cfg.evaluate("el => el.open") is True
    assert gen.evaluate("el => el.open") is False
