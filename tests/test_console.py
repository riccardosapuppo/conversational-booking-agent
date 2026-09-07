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


#: The service and the browser, started once for the whole file.
#:
#: One of each rather than one per class: starting a second service means a
#: second port, and two of them on one machine is a check that passes alone and
#: fails when the file is run in one go. What is not shared is the page — a
#: call is state, and two checks sharing one would be two checks where the
#: second only passes in the order the first left things.
STANDING: dict = {}


def setUpModule() -> None:
    if sync_playwright is None:
        raise unittest.SkipTest("Playwright is not installed here")

    if not free(PORT):
        raise unittest.SkipTest(f"something is already listening on {PORT}")

    STANDING["service"] = subprocess.Popen(
        [sys.executable, "-m", "booking_agent.service", "--port", str(PORT), "--no-open"],
        cwd=HERE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not answered(f"{WHERE}/health", seconds=30):
        raise unittest.SkipTest("the service did not come up")

    STANDING["playing"] = sync_playwright().start()

    try:
        STANDING["browser"] = STANDING["playing"].chromium.launch(channel="msedge")
    except Exception as why:  # pragma: no cover - depends on the machine
        raise unittest.SkipTest(f"Microsoft Edge would not start here: {why}")


def tearDownModule() -> None:
    browser = STANDING.pop("browser", None)
    if browser is not None:
        browser.close()

    playing = STANDING.pop("playing", None)
    if playing is not None:
        playing.stop()

    service = STANDING.pop("service", None)
    if service is None:
        return

    service.terminate()
    try:
        service.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        service.kill()


class Driving(unittest.TestCase):
    """A page of its own, opened on the caller's side and settled."""

    #: Something to run before the page's own scripts, for a check about
    #: behaviour that leaves no mark on the screen. Empty here: a page watched
    #: for no reason is a page not being tested as it ships.
    watching = ""

    def setUp(self) -> None:
        self.page = STANDING["browser"].new_page(viewport={"width": 1200, "height": 900})
        self.addCleanup(self.page.close)

        #: Anything the page threw and nobody caught. Collected here because it
        #: has to be listening before the page loads, and looked at where it
        #: matters — a page that works and throws on the way past is a page one
        #: browser away from not working.
        self.problems: list[str] = []
        self.page.on("pageerror", lambda why: self.problems.append(str(why)))

        if self.watching:
            self.page.add_init_script(self.watching)

        self.page.goto(f"{WHERE}/#caller", wait_until="networkidle")
        self.settled()

    # ------------------------------------------------------------- the page

    def settled(self) -> None:
        """Waits until no bubble is still waiting for words to go in it."""
        self.page.wait_for_function("() => !document.querySelector('.words.thinking')")

    def dials_again(self, press) -> None:
        """Does something that starts a new call, and waits for the new call.

        Not just for the click: "start again" and the channel control both hang
        up before they dial, so at the moment the click returns the transcript
        still belongs to the call that has gone and nothing is waiting for
        words yet. Asking whether the page has settled right then gets a
        truthful answer about the wrong call.

        Waited for by the reference rather than by the transcript being one
        bubble long. That was the first version of this and it is right until
        the call being replaced is *also* one bubble long — which is exactly
        what a page that has just been opened has, so the check that used it
        passed or failed depending on how fast the machine was.
        """
        was = self.page.locator("#call-reference").text_content()
        press()
        self.page.wait_for_function(
            "(was) => document.getElementById('call-reference').textContent !== was", arg=was
        )
        self.settled()

    def restart(self) -> None:
        self.dials_again(lambda: self.page.click("#again"))

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


class TheConsole(Driving):
    """The page, driven the way somebody uses it."""

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


# ─────────────────────────────────────────────────── the telephone, out loud

#: Watching what the page asks to be said.
#:
#: Sound is the one thing a browser driven by a script cannot hear, so what is
#: checked is what was *asked for* — and that turns out to be the interesting
#: half anyway: the words handed to the platform are the words `voice.py` made,
#: and whether they were asked for at all is the whole of the autoplay
#: question. The real methods are still called underneath, so nothing here is
#: testing a page that has been altered into working.
WATCHING = """
  window.__spoken = [];
  window.__cancels = 0;

  const speak = speechSynthesis.speak.bind(speechSynthesis);
  const cancel = speechSynthesis.cancel.bind(speechSynthesis);

  speechSynthesis.speak = (utterance) => {
    window.__spoken.push(utterance.text);
    return speak(utterance);
  };

  speechSynthesis.cancel = () => {
    window.__cancels += 1;
    return cancel();
  };
"""

#: A browser with no voice of its own. There are real ones, and this is how the
#: page they get is checked without going and finding one.
NO_VOICE = """
  delete window.speechSynthesis;
  delete window.SpeechSynthesisUtterance;
"""


class TheTelephoneIsHeard(Driving):
    """The difference between the two channels, made audible.

    Everything `voice.py` does was invisible on this page: choosing "the
    telephone" changed the words in a bubble somebody went on reading, so the
    question the page got asked was what the difference was supposed to be.
    These are about the answer being a thing you hear.
    """

    watching = WATCHING

    def spoken(self) -> list[str]:
        return self.page.evaluate("() => window.__spoken")

    def cancels(self) -> int:
        return self.page.evaluate("() => window.__cancels")

    def telephone(self) -> None:
        """Picks the telephone, and waits for the call that starts.

        A channel is a property of a call, so choosing one hangs the old call
        up and dials again. Only when it is not already chosen: a reload can
        bring the control back the way it was left, and selecting what is
        already selected starts nothing to wait for.
        """
        if self.page.locator("#channel").input_value() != "voice":
            self.dials_again(lambda: self.page.select_option("#channel", "voice"))

        self.settled()

    def test_nothing_is_said_before_anybody_has_asked_for_anything(self) -> None:
        """A page that talks the moment it loads is a page somebody closes.

        It is also a page browsers refuse to let talk: speech is blocked until
        something has been pressed, so a greeting spoken on arrival would be
        dropped without a word — a feature that half works, which is worse than
        one that is off. Both are answered by this never speaking first.
        """
        self.assertEqual(self.spoken(), [], "the page spoke on the way in")
        self.assertTrue(self.page.locator("#sound").is_hidden(), "a sound control on a chat")

    def test_and_not_even_when_it_opens_with_the_telephone_already_chosen(self) -> None:
        """The case the rule is actually for, and the reason it is a rule.

        A page that opens on chat is quiet whatever it believes about speaking
        first. A control can come back the way it was left, though — some
        browsers do that on a plain reload and all of them on the way back
        through history — and a page that read the channel and greeted out loud
        would then be talking to somebody who has pressed nothing, into a
        browser that will not let it and reports nothing when it refuses.

        Edge does not restore this control on a reload, so the page is served
        here with the telephone already selected, which is the same arrival.
        """
        self.page.route(
            f"{WHERE}/",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=(HERE / "web" / "index.html")
                .read_text(encoding="utf-8")
                .replace('<option value="voice">', '<option value="voice" selected>'),
            ),
        )

        self.page.goto(f"{WHERE}/", wait_until="networkidle")
        self.settled()

        self.assertEqual(self.page.locator("#channel").input_value(), "voice", "the page did not open on it")
        self.assertEqual(self.spoken(), [], "it spoke to somebody who had not asked for anything")
        self.assertFalse(self.page.locator("#sound").is_hidden(), "and offered no way to stop it")

        # And it speaks the moment there is a reason to.
        self.says("knee")
        self.assertEqual(self.spoken(), [self.last()])

    def test_picking_the_telephone_says_the_greeting_out_loud(self) -> None:
        self.telephone()

        self.assertEqual(self.spoken(), [self.last()], "the greeting was not said out loud")
        self.assertFalse(self.page.locator("#sound").is_hidden(), "no way to silence it")

        # It has to say where the voice comes from, and that nothing is being
        # listened to. A page that starts making sound and explains nothing is
        # a page somebody is right to be suspicious of.
        said = self.page.locator("#channel-says").text_content()
        self.assertIn("nothing leaves this machine", said.lower())

    def test_and_a_chat_says_nothing_at_all(self) -> None:
        """The channel is the whole of the difference, here as everywhere else."""
        self.says("knee")

        self.assertIn("MRI knee", self.last())
        self.assertEqual(self.spoken(), [], "a chat was read aloud")

    def test_what_is_said_is_what_the_voice_channel_made_of_it(self) -> None:
        """The payoff, and the reason any of this was worth doing.

        A listener cannot look back. So the times are sentences rather than a
        numbered list, and the reference is spelled out and then spelled again
        — and this is where that stops being a unit test of a function and
        becomes the thing somebody hears while holding a pen.
        """
        self.telephone()

        for words in ("MRI knee", "left, no contrast", "Anna Bianchi", "the second", "yes"):
            self.says(words)

        said = self.spoken()
        booked = said[-1]

        self.assertEqual(booked, self.last(), "the words heard are not the words on the screen")

        found = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", self.page.locator("#ended").text_content())
        self.assertIsNotNone(found, "the call did not end in a booking")

        spelled = " ".join("dash" if character == "-" else character for character in found.group(1))
        self.assertEqual(booked.count(spelled), 2, f"the reference was not said twice: {booked!r}")

        for one in said:
            with self.subTest(said=one[:40]):
                self.assertNotRegex(one, r"\d{1,2}:\d{2}", "a written time was read out")
                self.assertNotRegex(one, r"\d\)", "a numbered list was read out")

        self.assertTrue(
            any("o'clock" in one for one in said),
            "no time was ever spoken as a time",
        )

    def test_the_last_thing_said_is_cut_off_by_the_next(self) -> None:
        """Speech that queues is a page talking about the message before last.

        Cut off when the sentence is sent rather than when the reply arrives:
        waiting for the reply leaves half of the previous one still playing
        over somebody who has already moved on. So this asks for the silence to
        have happened before the reply could possibly have landed — the page
        holds every bubble empty for half a second, and this waits for less.
        """
        self.telephone()
        self.says("MRI knee")

        before = self.cancels()

        self.page.fill("#text", "left, no contrast")
        self.page.click("#send")

        try:
            self.page.wait_for_function(f"() => window.__cancels > {before}", timeout=least() - 100)
        except NeverTurnedUp:
            self.fail("the previous reply was still being said when the next one was sent")

        self.assertEqual(
            self.page.locator(".said .words.thinking").count(),
            1,
            "the reply had already arrived, so this proves nothing about cutting it off",
        )

        self.settled()

    def test_it_can_be_silenced_and_the_silence_survives_a_reload(self) -> None:
        """Having to silence a page twice is the insult that closes the tab."""
        self.telephone()
        self.assertTrue(self.spoken(), "there was nothing to silence")

        self.page.click("#sound")

        self.assertEqual(self.page.locator("#sound").get_attribute("aria-pressed"), "false")
        self.assertEqual(self.page.locator("#sound-says").text_content(), "sound off")

        said_before = len(self.spoken())
        self.says("MRI knee")
        self.assertEqual(len(self.spoken()), said_before, "it went on talking after being silenced")

        self.page.reload(wait_until="networkidle")
        self.settled()
        self.telephone()
        self.says("MRI knee")

        self.assertEqual(self.spoken(), [], "the silence lasted exactly one page")
        self.assertEqual(self.page.locator("#sound-says").text_content(), "sound off")


class WhereThereIsNoVoice(Driving):
    """A browser with no `speechSynthesis`, which is a real browser and not a
    hypothetical one. The page it gets is the page as it was."""

    watching = NO_VOICE

    def test_the_page_is_exactly_what_it_was(self) -> None:
        self.assertEqual(
            self.page.evaluate("() => typeof window.speechSynthesis"),
            "undefined",
            "the browser still has a voice, so this checks nothing",
        )

        self.dials_again(lambda: self.page.select_option("#channel", "voice"))

        self.assertTrue(self.page.locator("#sound").is_hidden(), "a sound control with nothing to say")

        # And it says so, rather than describing a telephone that talks.
        self.assertIn("to be read", self.page.locator("#channel-says").text_content())

        self.says("knee")
        self.assertIn("MRI knee", self.last())
        self.assertEqual(self.problems, [], "the page threw on a browser with no voice")


if __name__ == "__main__":
    unittest.main()
