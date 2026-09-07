"""A whole telephone call, replayed through the adapter without a telephone.

The claim this file exists to check is a specific one: that the messages a
switchboard sends arrive at the same three requests the console makes, and come
back as verbs a switchboard can run. Nothing about that needs a number, an
account or a line — it needs the messages, and `data/telephony/` holds a real
call's worth of them.

So the call is replayed from the first ring to the booking, against the real
service in memory, and what is checked at the end is not a transcript but the
diary: an appointment for the person who rang, at the time they picked. A
translation that looked right and booked nothing would pass a check written
against its own output, which is the way this could most easily have been
written to prove nothing.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from booking_agent.clinic.build import default
from booking_agent.service.api import build
from booking_agent.telephony.desk import Desk, Gone, Service
from booking_agent.telephony.jambonz import Jambonz, _transcript

HERE = Path(__file__).resolve().parents[1]
CALL = HERE / "data" / "telephony" / "one-whole-call.json"

MONDAY = date(2026, 9, 7)
BEFORE = datetime(2026, 9, 4, 9, 0)

#: The properties each verb is allowed to carry, from the published
#: documentation for that verb. A verb with a property outside this is either a
#: newly documented one somebody has read about, or an invented one — and the
#: second is the thing that makes a whole adapter untrustworthy, so it fails
#: here rather than being discovered by whoever wires it to a real switchboard.
DOCUMENTED = {
    "say": {"verb", "text", "earlyMedia", "loop", "synthesizer"},
    "gather": {
        "verb",
        "actionHook",
        "input",
        "bargein",
        "dtmfBargein",
        "finishOnKey",
        "interDigitTimeout",
        "listenDuringPrompt",
        "maxDigits",
        "minBargeinWordCount",
        "minDigits",
        "numDigits",
        "partialResultHook",
        "play",
        "recognizer",
        "say",
        "timeout",
    },
    "hangup": {"verb", "headers"},
}


def spelled(reference: str) -> str:
    """A reference the way somebody writing it down has to hear it."""
    return " ".join("dash" if character == "-" else character for character in reference)


def recorded() -> tuple[Jambonz, TestClient, list[tuple[str, str]]]:
    """An adapter over the real service, and a note of everything said to it.

    The note is the point of the wrapper: several of the checks below are about
    what was *not* sent to the agent — a silence, a transcript the switchboard
    was unsure of — and the only place that is visible is the boundary between
    the two.
    """
    client = TestClient(build(default(starting=MONDAY), clock=lambda: BEFORE))
    heard: list[tuple[str, str]] = []

    class Noting:
        """The service desk, with a notebook."""

        def __init__(self, desk: Desk) -> None:
            self._desk = desk

        def start(self, *, channel: str) -> dict[str, Any]:
            started = self._desk.start(channel=channel)
            heard.append(("start", channel))
            return started

        def said(self, call: str, text: str) -> dict[str, Any]:
            heard.append(("said", text))
            return self._desk.said(call, text)

        def hang_up(self, call: str) -> None:
            heard.append(("hang_up", call))
            self._desk.hang_up(call)

    return Jambonz(desk=Noting(Service(client))), client, heard


def frames() -> list[dict[str, Any]]:
    written = json.loads(CALL.read_text(encoding="utf-8"))
    return [step["message"] for step in written["call"]]


def verbs(ack: dict[str, Any] | None) -> list[dict[str, Any]]:
    assert ack is not None, "the switchboard was told nothing at all"
    return ack["data"]


def only(ack: dict[str, Any] | None) -> dict[str, Any]:
    said = verbs(ack)
    assert len(said) == 1, f"expected one verb, got {[one['verb'] for one in said]}"
    return said[0]


class AWholeCall(unittest.TestCase):
    """From the first ring to a booking, on the messages and nothing else."""

    def setUp(self) -> None:
        self.jambonz, self.client, self.heard = recorded()
        self.messages = frames()
        self.acks = [self.jambonz.heard(message) for message in self.messages]

    def test_it_ends_with_an_appointment_that_is_really_in_the_diary(self) -> None:
        """The check that cannot be satisfied by output alone.

        Everything else here reads what the adapter said. This reads what the
        clinic ended up with, which is the only thing a caller would have got.
        """
        booked = self.client.get("/bookings").json()["bookings"]

        self.assertEqual(len(booked), 1, "the call finished and booked nothing")
        self.assertEqual(booked[0]["patient"], "Mario Rossi")
        self.assertEqual(booked[0]["exams"], ["MRI-KNEE"])
        self.assertEqual(booked[0]["starts"], "2026-09-07T09:00")

    def test_the_first_message_is_answered_with_a_greeting_and_an_open_line(self) -> None:
        first = only(self.acks[0])

        self.assertEqual(first["verb"], "gather")
        self.assertEqual(first["input"], ["speech"])
        self.assertIn("Kesterby Diagnostic Centre", first["say"]["text"])

    def test_every_answer_quotes_the_message_it_answers(self) -> None:
        """A reply that loses the identifier is a reply to nothing."""
        for message, ack in zip(self.messages, self.acks):
            if ack is None:
                continue
            with self.subTest(type=message["type"]):
                self.assertEqual(ack["type"], "ack")
                self.assertEqual(ack["msgid"], message["msgid"])

    def test_the_last_thing_said_is_the_reference_and_then_the_call_ends(self) -> None:
        said, ending = verbs(self.acks[-2])

        booked = self.client.get("/bookings").json()["bookings"][0]

        self.assertEqual(said["verb"], "say")
        self.assertEqual(ending["verb"], "hangup")
        self.assertIn(spelled(booked["reference"]), said["text"])

        # Twice, because there is no scrolling back to a reference somebody has
        # half written down — and this is where that stops being a unit test of
        # a channel and starts being what a caller actually hears.
        self.assertEqual(said["text"].count(spelled(booked["reference"])), 2)

    def test_the_status_at_the_end_is_not_answered_and_lets_the_call_go(self) -> None:
        self.assertIsNone(self.acks[-1], "a status notification was acknowledged")
        self.assertEqual(len(self.jambonz), 0, "the adapter is still holding a finished call")

    def test_the_silence_reached_the_agent_as_nothing_at_all(self) -> None:
        """A timeout is not a sentence and is not sent as one.

        The caller said four things. The switchboard sent six hooks, one of
        which was a silence and one of which was the status at the end.
        """
        self.assertEqual(
            [text for what, text in self.heard if what == "said"],
            [
                "I need an MRI of the left knee without contrast",
                "Mario Rossi",
                "the first one",
                "yes",
            ],
        )

    def test_and_was_answered_by_asking_the_same_question_again(self) -> None:
        asked = only(self.acks[1])["say"]["text"]
        again = only(self.acks[2])["say"]["text"]

        self.assertEqual(asked, again)
        self.assertEqual(only(self.acks[2])["verb"], "gather")

    def test_the_call_was_had_on_the_telephone_and_it_shows(self) -> None:
        """The whole point, checked where it would be most easily lost.

        The channel is chosen once, when the call starts. If it were ever
        chat's, this would still be a working booking agent on a telephone
        line — reading out "1)" and "09:00" to somebody holding a pen.
        """
        self.assertIn(("start", "voice"), self.heard)

        for at, ack in enumerate(self.acks):
            for verb in verbs(ack) if ack else []:
                spoken = verb.get("text") or verb.get("say", {}).get("text", "")
                with self.subTest(message=at):
                    self.assertNotRegex(spoken, r"\d{1,2}:\d{2}", "a written time was read out")
                    self.assertNotRegex(spoken, r"\d\)", "a numbered list was read out")

        offered = only(self.acks[3])["say"]["text"]
        self.assertIn("o'clock in the morning", offered)

    def test_nothing_but_the_three_verbs_leaves_here_and_nothing_invented(self) -> None:
        for at, ack in enumerate(self.acks):
            for verb in verbs(ack) if ack else []:
                with self.subTest(message=at, verb=verb.get("verb")):
                    self.assertIn(verb["verb"], DOCUMENTED, "a verb nobody has read about")
                    self.assertLessEqual(
                        set(verb),
                        DOCUMENTED[verb["verb"]],
                        f"{verb['verb']} was given a property the documentation does not have",
                    )


class WhenNobodyIsThere(unittest.TestCase):
    def setUp(self) -> None:
        self.jambonz, self.client, self.heard = recorded()
        self.messages = frames()

    def hook(self, reason: str, **data: Any) -> dict[str, Any]:
        """A hook on the call the fixture starts, with a reason of our own."""
        one = self.messages[1]
        return {**one, "data": {"call_sid": one["call_sid"], "reason": reason, **data}}

    def test_a_second_silence_ends_the_call(self) -> None:
        """Asked once more, and then stopped.

        Somebody who has not answered twice has put the handset on the table.
        A machine asking a third time is a machine nobody is listening to, and
        a line nobody is on is a line the clinic is paying for.
        """
        self.jambonz.heard(self.messages[0])

        self.assertEqual(only(self.jambonz.heard(self.hook("timeout")))["verb"], "gather")
        self.assertEqual(only(self.jambonz.heard(self.hook("timeout")))["verb"], "hangup")

        self.assertEqual(len(self.jambonz), 0)
        self.assertIn("hang_up", [what for what, _ in self.heard])

    def test_a_transcript_the_switchboard_is_unsure_of_is_treated_as_silence(self) -> None:
        """It does not guess, and neither does the thing in front of it.

        A recogniser saying "this is what I heard, and I am not confident"
        carries a sentence. Sending it on would put a guess into a booking,
        which is the one thing the whole of this project is arranged against —
        so it is answered the way an empty line is.
        """
        self.jambonz.heard(self.messages[0])

        unsure = self.hook(
            "stt-low-confidence",
            speech={"is_final": True, "alternatives": [{"confidence": 0.21, "transcript": "yes"}]},
        )

        self.assertEqual(only(self.jambonz.heard(unsure))["verb"], "gather")
        self.assertEqual([text for what, text in self.heard if what == "said"], [])

    def test_a_hook_for_a_call_this_never_answered_is_not_turned_into_one(self) -> None:
        stray = {**self.messages[1], "call_sid": "0a1b2c3d-0000-0000-0000-000000000000"}

        self.assertEqual(only(self.jambonz.heard(stray))["verb"], "hangup")
        self.assertEqual(self.heard, [], "a stray hook started a conversation")

    def test_a_message_of_a_kind_it_does_not_handle_is_left_alone(self) -> None:
        """Not answered, and not guessed at.

        Several other message types are published and none of them is handled
        here. Replying to one with verbs invented for the occasion is how an
        adapter starts describing an interface that does not exist.
        """
        for kind in ("session:reconnect", "session:redirect", "verb:status", "error"):
            with self.subTest(type=kind):
                self.assertIsNone(self.jambonz.heard({"type": kind, "msgid": "x", "call_sid": "y"}))

        self.assertEqual(self.heard, [])


class WhatTheSwitchboardHeard(unittest.TestCase):
    """Reading a transcript out of a payload, in both shapes it is published in."""

    def test_the_alternatives_under_speech(self) -> None:
        said = _transcript(
            {
                "reason": "speechDetected",
                "speech": {"is_final": True, "alternatives": [{"transcript": "MRI knee "}]},
            }
        )
        self.assertEqual(said, "MRI knee")

    def test_and_the_alternatives_under_a_list_of_transcripts(self) -> None:
        said = _transcript(
            {
                "reason": "speechDetected",
                "speech": {
                    "is_final": True,
                    "transcripts": [{"alternatives": [{"transcript": "MRI knee"}]}],
                },
            }
        )
        self.assertEqual(said, "MRI knee")

    def test_an_interim_result_is_not_half_a_sentence_booked_as_a_whole_one(self) -> None:
        said = _transcript(
            {
                "reason": "speechDetected",
                "speech": {"is_final": False, "alternatives": [{"transcript": "MRI"}]},
            }
        )
        self.assertEqual(said, "")

    def test_and_a_payload_with_nothing_in_it_is_silence_rather_than_a_crash(self) -> None:
        for data in ({}, {"reason": "speechDetected"}, {"reason": "speechDetected", "speech": {}}):
            with self.subTest(data=data):
                self.assertEqual(_transcript(data), "")


class TheDeskItSpeaksThrough(unittest.TestCase):
    """The three requests, and what happens when there is nothing to make them to."""

    def setUp(self) -> None:
        self.client = TestClient(build(default(starting=MONDAY), clock=lambda: BEFORE))
        self.desk = Service(self.client)

    def test_it_makes_the_same_three_requests_the_console_makes(self) -> None:
        """Named off the protocol rather than listed here.

        "The same three, and no fourth" is the whole argument for this package
        being a client rather than a second entrance, and a sentence cannot
        notice a fourth method being added to a class.
        """
        self.assertEqual(
            sorted(name for name in vars(Desk) if not name.startswith("_")),
            ["hang_up", "said", "start"],
        )

    def test_a_call_that_has_been_let_go_of_is_one_exception(self) -> None:
        with self.assertRaises(Gone):
            self.desk.said("no-such-call", "hello")

    def test_and_so_is_one_that_has_already_finished(self) -> None:
        call = self.desk.start(channel="voice")["call"]
        self.desk.said(call, "I want to change a booking")

        with self.assertRaises(Gone):
            self.desk.said(call, "hello")

    def test_hanging_up_twice_is_not_a_fault(self) -> None:
        call = self.desk.start(channel="voice")["call"]

        self.desk.hang_up(call)
        self.desk.hang_up(call)


class TheCallOnFile(unittest.TestCase):
    """The fixture itself, which is evidence and has to look like it."""

    def setUp(self) -> None:
        self.written = json.loads(CALL.read_text(encoding="utf-8"))

    def test_it_is_a_call_from_a_ring_to_a_handset_going_down(self) -> None:
        kinds = [step["message"]["type"] for step in self.written["call"]]

        self.assertEqual(kinds[0], "session:new")
        self.assertEqual(kinds[-1], "call:status")
        self.assertIn("verb:hook", kinds)

    def test_the_messages_carry_nothing_a_switchboard_would_not_send(self) -> None:
        """The notes are outside the messages, and stay there.

        A note inside one is a field a reader would take for part of the
        protocol, which is the same kind of lie as an invented verb property —
        quieter, and harder to catch by reading the adapter.
        """
        for step in self.written["call"]:
            with self.subTest(msgid=step["message"]["msgid"]):
                self.assertLessEqual(
                    set(step["message"]),
                    {"type", "msgid", "call_sid", "hook", "data", "b3"},
                )

    def test_the_numbers_in_it_belong_to_nobody(self) -> None:
        """Reserved for fiction, and checked, because a fixture is published.

        01632 960000 to 960999 is the range set aside so that a number written
        into a story reaches nobody when somebody dials it.
        """
        arrived = self.written["call"][0]["message"]["data"]

        for number in (arrived["from"], arrived["to"]):
            with self.subTest(number=number):
                self.assertRegex(number, r"^\+441632960\d{3}$")

    def test_every_call_sid_in_it_is_the_same_call(self) -> None:
        sids = set(re.findall(r'"call_sid": "([^"]+)"', CALL.read_text(encoding="utf-8")))
        self.assertEqual(len(sids), 1, f"more than one call is written into one call: {sids}")


if __name__ == "__main__":
    unittest.main()
