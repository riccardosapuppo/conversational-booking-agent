"""The pictures in the README, made rather than taken.

    python -m tools.screenshots

Nothing here photographs the screen. It starts its own service on its own port,
opens the console in a browser, drives it, and captures **the page** — so
whatever else happens to be on this machine cannot end up in a file about to be
pushed to a repository.

Generated rather than kept by hand so they cannot quietly stop matching the
thing they are pictures of. Re-run whenever the console changes.

It drives **Microsoft Edge**, already on the machine, rather than downloading a
browser: `channel="msedge"`. That is the right trade on a workstation and
impossible on a runner with no browser, which is why this is not in CI. It says
so and stops if Playwright is not installed, rather than reporting a success it
did not earn.

    pip install -r requirements-checks.txt
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent.parent
DOCS = HERE / "docs"
PORT = 8099
WHERE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed here, so the pictures cannot be retaken.")
        print("  pip install -r requirements-checks.txt")
        return 2

    DOCS.mkdir(exist_ok=True)

    if listening(PORT):
        print(f"Something is already listening on {PORT}, and this will not photograph a stranger.")
        return 2

    service = subprocess.Popen(
        [sys.executable, "-m", "booking_agent.service", "--port", str(PORT), "--no-open"],
        cwd=HERE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not answered(f"{WHERE}/health", seconds=30):
            print("The service did not come up.")
            return 1

        with sync_playwright() as playing:
            browser = playing.chromium.launch(channel="msedge")

            try:
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1080},
                    device_scale_factor=2,
                    reduced_motion="reduce",
                )
                page.goto(f"{WHERE}/#caller", wait_until="networkidle")
                settled(page)

                # 1. The caller's side, and the ambiguity that is the reason
                #    the agent asks rather than guesses. Nothing of the clinic
                #    is on this screen, because a caller has none of it.
                page.click('[data-say="knee"]')
                settled(page)
                page.screenshot(path=str(DOCS / "console.png"), full_page=True)
                say("console.png")

                # 2. What is reading, with the seam opened out. The strip is
                #    filled from /reading, so this picture is of the reader the
                #    service actually built rather than of a sentence.
                page.click(".seam summary")
                page.wait_for_timeout(400)
                page.locator(".reading").screenshot(path=str(DOCS / "reading.png"))
                say("reading.png")
                page.click(".seam summary")

                # 3. A whole booking, and then crossing to the other side to
                #    see what it cost the diary.
                page.click("#again")
                settled(page)

                for words in [
                    "MRI knee",
                    "left, no contrast",
                    "Anna Bianchi",
                    "the second",
                    "yes",
                ]:
                    page.fill("#text", words)
                    page.click("#send")
                    settled(page)

                page.click('[data-side="desk"]')
                page.click('[data-tab="bookings"]')
                page.wait_for_timeout(500)
                page.screenshot(path=str(DOCS / "booked.png"), full_page=True)
                say("booked.png")

                # 4. The diary, which is where "free" turns out to mean "free
                #    for something".
                page.click('[data-tab="diary"]')
                page.select_option("#minutes", "60")
                page.wait_for_timeout(800)
                page.locator(".clinic").screenshot(path=str(DOCS / "diary.png"))
                say("diary.png")

                # 5. Stopping, and fetching a person. Drawn as the right
                #    outcome rather than as a failure, which is what it is —
                #    and the end of the call, which is what it also is: the
                #    input closes and the page says so.
                page.click('[data-side="caller"]')
                page.click("#again")
                settled(page)
                page.fill("#text", "put me through to someone")
                page.click("#send")
                settled(page)
                page.locator(".talk").screenshot(path=str(DOCS / "handover.png"))
                say("handover.png")

                page.close()
            finally:
                browser.close()
    finally:
        service.terminate()
        try:
            service.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            service.kill()

    print(f"\nThe pictures in the README are of the console as it is now: {DOCS}")
    return 0


def settled(page) -> None:
    """Waits for the reply rather than for a number of milliseconds.

    Every bubble stands empty for at least half a second now, so that the loader
    in it can be seen at all; a script that guessed at how long to wait was
    guessing before that changed and would have had to be re-guessed after it.
    What it actually wants is the reply, and the page says plainly when there is
    not one yet — an empty bubble is a bubble still waiting.
    """
    page.wait_for_function("() => !document.querySelector('.words.thinking')")


def say(name: str) -> None:
    print(f"  docs/{name}")


def listening(port: int) -> bool:
    with socket.socket() as one:
        one.settimeout(1)
        return one.connect_ex(("127.0.0.1", port)) == 0


def answered(url: str, *, seconds: int) -> bool:
    """Waits for an answer, not for the port.

    A server binds its port before it is ready to answer, so a connect proves
    the socket is open and nothing else.
    """
    until = time.monotonic() + seconds

    while time.monotonic() < until:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)

    return False


if __name__ == "__main__":
    raise SystemExit(main())
