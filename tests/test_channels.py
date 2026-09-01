"""Saying the same reply two ways.

The last test in here is the one that matters. It runs every conversation in
data/conversations through the agent, takes every reply it gives, and puts all
of them through the telephone channel — then fails on anything left that cannot
be said out loud. It is the same idea as the transcripts themselves: check
against everything the thing actually produces, not against the four examples
that were in mind while writing it.
"""

from __future__ import annotations

import re
import unittest
from datetime import date, datetime
from pathlib import Path

from booking_agent.channels import Chat, Voice, for_name
from booking_agent.clinic.build import default
from booking_agent.clinic.diary import reference
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Rules
from booking_agent.conversation.state import new
from tools.transcripts import lines_of


class ChoosingAChannel(unittest.TestCase):
    def test_by_name(self) -> None:
        self.assertIsInstance(for_name("voice"), Voice)
        self.assertIsInstance(for_name("PHONE"), Voice)
        self.assertIsInstance(for_name("chat"), Chat)

    def test_anything_unrecognised_is_still_answered(self) -> None:
        # A reply in the wrong shape beats no reply at all.
        self.assertIsInstance(for_name("carrier pigeon"), Chat)
        self.assertIsInstance(for_name(""), Chat)


class OnAScreen(unittest.TestCase):
    def test_the_list_stays_a_list(self) -> None:
        said = Chat().say("I have these:\n1) Monday 07 September at 09:00\n\nWhich suits you?")

        self.assertIn("1) Monday 07 September at 09:00", said)
        self.assertEqual(len(said.splitlines()), 3)


class DownATelephone(unittest.TestCase):
    def test_the_list_becomes_sentences(self) -> None:
        said = Voice().say(
            "I have these:\n1) Monday 07 September at 09:00\n2) Tuesday 08 September at 14:30"
        )

        self.assertIn("The first is Monday", said)
        self.assertIn("The second is Tuesday", said)
        self.assertNotIn("1)", said)

    def test_a_time_is_said_with_the_part_of_the_day(self) -> None:
        # "Seven" on its own gets one caller in twelve hours early.
        self.assertIn("nine o'clock in the morning", Voice().say("at 09:00"))
        self.assertIn("two thirty in the afternoon", Voice().say("at 14:30"))
        self.assertIn("nine oh five in the morning", Voice().say("at 09:05"))
        self.assertIn("twelve o'clock in the afternoon", Voice().say("at 12:00"))

    def test_a_date_is_said_rather_than_spelled_out(self) -> None:
        self.assertIn("the seventh of September", Voice().say("Monday 07 September"))
        self.assertIn("the twenty-second of March", Voice().say("Sunday 22 March"))
        self.assertIn("the third of May", Voice().say("Friday 03 May"))

    def test_a_reference_is_spelled_and_said_twice(self) -> None:
        said = Voice().say("Your reference is KFR-4T9.")

        self.assertIn("K F R dash 4 T 9", said)
        self.assertEqual(said.count("K F R dash 4 T 9"), 2)
        self.assertNotIn("KFR-4T9", said)


class AReferenceSomebodyHasToWriteDown(unittest.TestCase):
    def test_it_has_nothing_in_it_that_sounds_like_something_else(self) -> None:
        # O and zero, I and one, S and five, B and eight. And no vowels, so a
        # run of random letters cannot spell anything the clinic would rather
        # not read out.
        for _ in range(500):
            made = reference()
            with self.subTest(reference=made):
                self.assertRegex(made, r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")
                self.assertFalse(set(made) & set("OI0158AEIOU"))

    def test_it_does_not_hand_out_one_that_is_taken(self) -> None:
        taken = {reference() for _ in range(200)}
        self.assertNotIn(reference(taken), taken)


class EverythingTheAgentEverSays(unittest.TestCase):
    """The check that is not driven by the examples in this file."""

    #: What is left in a reply that a listener cannot use.
    unsayable = (
        (re.compile(r"\d+\)"), "a numbered list"),
        (re.compile(r"\d{1,2}:\d{2}"), "a time written in digits"),
        # A month after the digits, and not just any capitalised word: "12
        # Example Street" is a house number, and a house number is perfectly
        # sayable. The first version of this line failed the opening hours.
        (
            re.compile(
                r"\b\d{2} (January|February|March|April|May|June|July"
                r"|August|September|October|November|December)\b"
            ),
            "a date written in digits",
        ),
        (re.compile(r"[A-Z0-9]{3}-[A-Z0-9]{3}"), "an unspelled reference"),
        (re.compile(r"[()]"), "a bracket"),
        (re.compile(r"—"), "an em dash"),
        (re.compile(r"\n"), "a line break"),
    )

    def replies(self) -> list[tuple[str, str]]:
        """Every reply the agent gives across every transcript that ships."""
        here = Path(__file__).resolve().parents[1]
        folder = here / "data" / "conversations"

        collected: list[tuple[str, str]] = []
        for path in sorted(folder.glob("*.txt")):
            clinic = default(starting=date(2026, 9, 7))
            now = datetime(2026, 9, 4, 9, 0)
            agent = Agent(
                catalogue=clinic.catalogue,
                diary=clinic.diary,
                reader=Rules(clinic.catalogue),
                clinic_name=clinic.name,
                opening_hours=clinic.opening_hours,
                address=clinic.address,
            )
            talk = new(path.stem)

            for said in lines_of(path):
                collected.append((path.stem, agent.reply_to(talk, said, now=now)))
                if talk.over:
                    break

        return collected

    def test_there_are_replies_to_check(self) -> None:
        # Without this, a folder that had gone missing would make everything
        # below pass by having nothing to fail on.
        self.assertGreater(len(self.replies()), 20)

    def test_every_one_of_them_can_be_said_out_loud(self) -> None:
        voice = Voice()

        for where, reply in self.replies():
            if not reply:
                continue
            said = voice.say(reply)
            for pattern, what in self.unsayable:
                with self.subTest(transcript=where, leaves=what):
                    self.assertIsNone(pattern.search(said), f"{what} left in: {said}")

    def test_and_none_of_them_come_back_empty(self) -> None:
        # Silence is the one reply a caller cannot respond to.
        for where, reply in self.replies():
            with self.subTest(transcript=where):
                self.assertTrue(Chat().say(reply).strip())
                self.assertTrue(Voice().say(reply).strip())


if __name__ == "__main__":
    unittest.main()
