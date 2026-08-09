"""Phase 4c UI test: best-of-N take picker workflow."""


def test_best_of_n_workflow(open_page):
    page = open_page("me@x.com")
    bodies = []

    def handler(route):
        bodies.append(route.request.post_data or "")
        route.fulfill(status=200, content_type="audio/wav",
                      headers={"Content-Disposition": 'attachment; filename="x.wav"'},
                      body=b"RIFF0000WAVEfakeaudio")

    page.route("**/tts", handler)
    page.goto("/")
    page.wait_for_load_state("networkidle")
    page.fill("#text", "hello world variation test")
    page.fill("#best-of-n", "3")
    page.click("#best-of-n-btn")

    page.wait_for_selector("#best-of-n-results .history-row", timeout=15000)
    assert page.locator("#best-of-n-results .history-row").count() == 3
    # All three trial takes were generated with save_to_history disabled.
    trials = [b for b in bodies if '"save_to_history":false' in b]
    assert len(trials) == 3
    # Each used a distinct seed.
    assert '"seed":1000' in "".join(bodies) and '"seed":1002' in "".join(bodies)

    # Keeping a take re-generates it with history enabled.
    page.locator("#best-of-n-results button", has_text="Keep").first.click()
    page.wait_for_timeout(600)
    assert any('"save_to_history":true' in b for b in bodies)
