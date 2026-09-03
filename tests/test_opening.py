"""When the console is opened, and — mostly — when it is not.

Every refusal below is a way of being wrong that is hard to diagnose from the
symptom. A launcher that blocks on a runner turns a green job into one that
hangs for six hours; a window opening on a server's console because a
supervisor started the service is a surprise nobody can trace to a line of
code.

Nothing here opens anything: the decision is the part worth testing, and the
opening is one line beneath it.
"""

from __future__ import annotations

import unittest

from booking_agent.service.opening import open_it

WHERE = "http://127.0.0.1:8000/"


class WhenItOpensTheConsole(unittest.TestCase):
    def test_not_when_told_not_to_on_the_command_line(self) -> None:
        opened, why = open_it(WHERE, no_open=True, environ={}, is_tty=True)
        self.assertFalse(opened)
        self.assertIn("--no-open", why)

    def test_not_when_told_not_to_in_the_environment(self) -> None:
        opened, why = open_it(WHERE, environ={"NO_OPEN": "1"}, is_tty=True)
        self.assertFalse(opened)
        self.assertIn("NO_OPEN", why)

    def test_and_no_open_zero_means_what_it_says(self) -> None:
        """A guard that reads "0" as "yes" is why people file bugs about flags."""
        opened, why = open_it(WHERE, environ={"NO_OPEN": "0", "CI": "true"}, is_tty=True)
        self.assertFalse(opened)
        self.assertIn("CI", why, "NO_OPEN=0 should have been ignored, leaving CI to refuse")

    def test_not_in_ci_where_there_is_no_browser(self) -> None:
        opened, why = open_it(WHERE, environ={"CI": "true"}, is_tty=True)
        self.assertFalse(opened)
        self.assertIn("CI", why)

    def test_not_when_nothing_is_attached_to_the_terminal(self) -> None:
        opened, why = open_it(WHERE, environ={}, is_tty=False)
        self.assertFalse(opened)
        self.assertIn("terminal", why)

    def test_it_always_says_why_not_because_silence_reads_as_breakage(self) -> None:
        for no_open, environ, is_tty in (
            (True, {}, True),
            (False, {"NO_OPEN": "1"}, True),
            (False, {"CI": "1"}, True),
            (False, {}, False),
        ):
            opened, why = open_it(WHERE, no_open=no_open, environ=environ, is_tty=is_tty)
            self.assertFalse(opened)
            self.assertGreater(len(why), 4, "a refusal with no reason in it")


if __name__ == "__main__":
    unittest.main()
