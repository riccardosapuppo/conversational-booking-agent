"""The console, driven in a browser, because this is where it went wrong.

Everything else in `tests/` can be checked without one: the agent, the diary and
the service are Python, and a test client is a fair stand-in for a caller. The
page is not. Three of the things somebody found by using it were true of the
page alone — the service was answering correctly the whole time, with a 409 and
a sentence saying why — and no amount of reading `index.html` as text would have
caught any of them, because what was wrong was what the page *did* with a
correct answer.

So this starts the real service, opens the real console in a real browser, and
presses the buttons. It drives **Microsoft Edge** already on the machine
(`channel="msedge"`), the same as `tools/screenshots.py`, rather than
downloading one — which is the right trade on a workstation and impossible on a
runner with no browser. On a machine without either it says so and skips, which
is worth saying plainly: **these checks do not run in CI**, and the day the
console breaks again in a way only a browser can see, CI will be green. The
alternative was to check nothing.

    pip install -r requirements-checks.txt
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as NeverTurnedUp, sync_playwright
except ImportError:  # pragma: no cover - the state CI is in
    sync_playwright = None
    NeverTurnedUp = TimeoutError

HERE = Path(__file__).resolve().parents[1]

#: A port of its own, so a service left running from `tools.screenshots` or a
#: development server on 8000 is neither photographed nor disturbed.
PORT = 8098
WHERE = f"http://127.0.0.1:{PORT}"

#: The sentence that ends a call. The agent cannot tell who is on the telephone,
#: so it will not touch a booking that already exists — it says so and fetches a
#: person, and the call is over from that reply onwards.
ENDS_IT = "I want to change a booking"


def least() -> int:
    """The page's own minimum, read out of the page.

    Copied into this file it would be a second opinion about one number, and the
    first thing to go stale the day somebody decides half a second is too long.
    """
    source = (HERE / "web" / "console.js").read_text(encoding="utf-8")
    found = re.search(r"const LEAST = (\d+);", source)
    assert found is not None, "console.js no longer says how long a bubble waits"
    return int(found.group(1))


def free(port: int) -> bool:
    with socket.socket() as one:
        one.settimeout(1)
        return one.connect_ex(("127.0.0.1", port)) != 0


def answered(url: str, *, seconds: int) -> bool:
    """Waits for an answer, not for the port: a server binds before it serves."""
    from urllib.request import urlopen

    until = time.monotonic() + seconds

    while time.monotonic() < until:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)

    return False


@unittest.skipUnless(sync_playwright, "Playwright is not installed here")
class TheConsole(unittest.TestCase):
    """One service and one browser for the lot; a fresh page for each check.

    A page each because a call is state and these are about how a call ends: two
    of them sharing one would be two tests where the second only passes in the
    order the first left things.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not free(PORT):
            raise unittest.SkipTest(f"something is already listening on {PORT}")

        cls.service = subprocess.Popen(
            [sys.executable, "-m", "booking_agent.service", "--port", str(PORT), "--no-open"],
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.addClassCleanup(cls.stop)

        if not answered(f"{WHERE}/health", seconds=30):
            raise unittest.SkipTest("the service did not come up")

        cls.playing = sync_playwright().start()
        cls.addClassCleanup(cls.playing.stop)

        try:
            cls.browser = cls.playing.chromium.launch(channel="msedge")
        except Exception as why:  # pragma: no cover - depends on the machine
            raise unittest.SkipTest(f"Microsoft Edge would not start here: {why}")

        cls.addClassCleanup(cls.browser.close)

    @classmethod
    def stop(cls) -> None:
        cls.service.terminate()
        try:
            cls.service.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            cls.service.kill()

    def setUp(self) -> None:
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        self.addCleanup(self.page.close)
        self.page.goto(f"{WHERE}/#caller", wait_until="networkidle")
        self.settled()

    # ------------------------------------------------------------- the page

    def settled(self) -> None:
        """Waits until no bubble is still waiting for words to go in it."""
        self.page.wait_for_function("() => !document.querySelector('.words.thinking')")

    def restart(self) -> None:
        """Presses "start again", and waits for the call it starts.

        Not just for the click: that button hangs up before it dials, so at the
        moment it returns the transcript still belongs to the call that has
        gone and nothing is waiting for words yet. Asking whether the page has
        settled right then gets a truthful answer about the wrong call.
        """
        self.page.click("#again")
        self.page.wait_for_function("() => document.querySelectorAll('#said li').length === 1")
        self.settled()

    def says(self, words: str) -> None:
        self.page.fill("#text", words)
        self.page.click("#send")
        self.settled()

    def bubbles(self) -> list[str]:
        return self.page.locator(".said li .words").all_text_contents()

    def last(self) -> str:
        said = self.bubbles()
        self.assertTrue(said, "the transcript is empty")
        return said[-1]

    # -------------------------------------------- a call that has finished

    def test_a_finished_call_puts_its_input_away(self) -> None:
        """The defect, from the caller's end.

        Every reply carries `over`, and the page read past it: after a handover
        the input still invited a sentence, the service refused it — rightly —
        and a bubble appeared with nothing in it. The input going quiet is the
        half of the fix somebody sees before they type.
        """
        self.says(ENDS_IT)

        self.assertTrue(self.page.locator("#text").is_disabled(), "still asking for a sentence")
        self.assertTrue(self.page.locator("#send").is_disabled(), "still offering to send one")

        for button in self.page.locator("[data-say]").all():
            self.assertTrue(
                button.is_disabled(),
                f'"{button.text_content()}" still says something into a call that has ended',
            )

        # And the way out is still there, because there has to be one.
        self.assertFalse(self.page.locator("#again").is_disabled(), "no way to start a new call")

    def test_and_says_which_ending_it_was_rather_than_only_stopping(self) -> None:
        """An input that goes quiet and says nothing is a page that has broken.

        Handing to a person is an outcome of this agent and not a failure of it,
        so it has to read as one: the words say a person has the call, and the
        line is drawn in the colour this page uses for a handover rather than in
        any colour it uses for an error.
        """
        self.says(ENDS_IT)

        ended = self.page.locator("#ended")
        self.assertTrue(ended.is_visible(), "the call stopped and the page did not say why")

        said = ended.text_content().lower()
        self.assertIn("person", said, f"it does not say who has the call now: {said!r}")
        self.assertIn("start again", said, f"it does not say how to go on: {said!r}")
        self.assertEqual(ended.get_attribute("data-how"), "handed")

    def test_and_will_not_take_a_sentence_pushed_past_it(self) -> None:
        """Greying an input is a hint, not a rule.

        The rule is in the one function everything that speaks goes through, so
        this asks the form to submit itself with the input filled in by hand —
        the only way left in — and expects the transcript not to move. Without
        that guard the next thing added to this page that says something is a
        second way into a call that has ended.
        """
        self.says(ENDS_IT)
        before = self.bubbles()

        self.page.evaluate(
            """() => {
              const text = document.getElementById('text');
              text.disabled = false;
              text.value = 'ciao';
              document.getElementById('say-form').requestSubmit();
            }"""
        )
        self.page.wait_for_timeout(least() * 2)

        self.assertEqual(self.bubbles(), before, "a finished call took another sentence")

    def test_and_start_again_opens_the_call_back_up(self) -> None:
        self.says(ENDS_IT)
        self.restart()

        self.assertFalse(self.page.locator("#text").is_disabled(), "a new call with no way to talk")
        self.assertFalse(self.page.locator("#ended").is_visible(), "the last call's ending, on this one")

        self.says("knee")
        self.assertIn("MRI knee", self.last())

    # ------------------------------------------------- a bubble that is not

    def test_a_refusal_goes_into_the_bubble_that_was_waiting_for_it(self) -> None:
        """The other half: whatever comes back, the bubble is not left empty.

        The 409 is intercepted rather than provoked, because the fix above means
        a caller can no longer reach one — and the shape being handed back is
        not invented here: `test_service.py` pins the status and the sentence
        the service actually sends, and this is that.

        The words are the service's own. A page that writes its own explanation
        of a state it is not keeping is a page that will eventually explain it
        wrongly, in a sentence nobody can grep for.
        """
        detail = "this call is handed_over"
        self.page.route(
            "**/calls/*/said",
            lambda route: route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": detail}),
            ),
        )

        self.says("hello")

        self.assertTrue(self.last().strip(), "the bubble is empty")
        self.assertIn(detail, self.last())
        self.assertNotIn("undefined", self.last())

    def test_and_so_does_a_service_that_never_answered_at_all(self) -> None:
        """No response, no `detail`, and still not an empty bubble."""
        self.page.route("**/calls/*/said", lambda route: route.abort())

        self.says("hello")

        self.assertTrue(self.last().strip(), "the bubble is empty")
        self.assertIn("did not answer", self.last())

    # ------------------------------------------------------- the loader

    def test_the_loader_is_seen_even_though_the_reply_is_instant(self) -> None:
        """It was there all along and lasted less than a frame.

        Rules and a diary in memory answer in single milliseconds, so the bubble
        was filled before the dots were ever painted: the thing put there to say
        "something is happening" only ever appeared when nothing was.
        """
        started = time.monotonic()
        self.page.click('[data-say="knee"]')

        self.assertEqual(
            self.page.locator(".said .words.thinking").count(),
            1,
            "the bubble was filled before anybody could see it was empty",
        )

        self.settled()
        took = (time.monotonic() - started) * 1000

        self.assertGreaterEqual(took + 50, least(), f"the reply landed after {took:.0f}ms")

    def test_and_the_same_floor_is_under_start_again(self) -> None:
        """One route with it and another without is worse than neither.

        The greeting, the form, the try-this buttons and this button all put a
        bubble up, and a page where the wait is visible on some of them says
        that the ones without it were faster — which they were not.
        """
        started = time.monotonic()
        self.page.click("#again")

        # Waited for rather than counted: this button hangs the old call up
        # before it starts a new one, so the bubble is a moment behind the
        # click. The clock still runs from the click, which makes the floor
        # measured here the whole of what somebody waits.
        try:
            self.page.wait_for_selector(".said .words.thinking", timeout=2000)
        except NeverTurnedUp:
            self.fail("the greeting was in its bubble before the loader was ever on the screen")

        self.settled()
        took = (time.monotonic() - started) * 1000

        self.assertGreaterEqual(took + 50, least(), f"the greeting landed after {took:.0f}ms")


if __name__ == "__main__":
    unittest.main()
