"""The MCP server, through a client of the protocol and never by calling the tools.

Two ways of reaching it, both the SDK's own client. Most of these go through
an in-memory transport carrying the protocol's messages, initialize handshake
and all, so they are quick and the clock can be handed in. The last ones start
``python -m booking_agent.mcp`` as a process and talk to it down its standard
input and output, which is exactly what a model host does, and read the clock
the server reads.

What matters here is not that the tools work for the inputs they were written
for. It is that every rule the conversation keeps, the server keeps too, when
the thing on the other end is a model that can send any arguments at all; and
that the bookings the golden calls expect come out of the diary the same when
they are made through this door.
"""

from __future__ import annotations

import json
import sys
import typing
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from mcp import Client, StdioServerParameters

from booking_agent.clinic.build import default
from booking_agent.clinic.catalogue import Side
from booking_agent.conversation.state import Escalation
from booking_agent.mcp.server import build
from tools.evals import differences, load

HERE = Path(__file__).resolve().parents[1]
MONDAY = date(2026, 9, 7)
FRIDAY_BEFORE = datetime(2026, 9, 4, 9, 0)

TOOLS = {
    "clinic_information",
    "search_exams",
    "find_slots",
    "hold_slot",
    "confirm_booking",
    "release_hold",
    "hand_over",
}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def said(result) -> str:
    return " ".join(part.text for part in result.content if getattr(part, "type", "") == "text")


def _connected(test):
    """Runs one test inside a client connected to a server of its own.

    Opened and closed around the test rather than in setUp and tearDown: the
    client's task group has to be left by the task that entered it, and the
    test case runs those three in different tasks.
    """

    async def run(self) -> None:
        async with Client(build(self.clinic, clock=self.clock), mode="legacy") as client:
            self.client = client
            await test(self)

    run.__name__ = test.__name__
    run.__doc__ = test.__doc__
    return run


class Talking(unittest.IsolatedAsyncioTestCase):
    """A server of its own for every test, over the protocol, with the clock stopped."""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for name, member in list(vars(cls).items()):
            if name.startswith("test_"):
                setattr(cls, name, _connected(member))

    def setUp(self) -> None:
        self.clinic = default(starting=MONDAY)
        self.clock = Clock(FRIDAY_BEFORE)

    async def call(self, tool: str, **arguments):
        return await self.client.call_tool(tool, arguments)

    async def ok(self, tool: str, **arguments) -> dict:
        result = await self.call(tool, **arguments)
        self.assertFalse(result.is_error, f"{tool} refused: {said(result)}")
        self.assertIsNotNone(result.structured_content, f"{tool} answered without a structured result")
        return result.structured_content

    async def refused(self, tool: str, *, saying: str, **arguments) -> str:
        result = await self.call(tool, **arguments)
        self.assertTrue(result.is_error, f"{tool} {arguments} was not refused")
        self.assertIn(saying, said(result))
        return said(result)

    async def kept(self, **arguments) -> dict:
        request = {"exam": "MRI-KNEE", "side": "left", "contrast": False, "room": "MR1", "starts": "2026-09-07T09:00:00"}
        request.update(arguments)
        return await self.ok("hold_slot", **request)


class WhatIsOnOffer(Talking):
    """The tools as a model sees them before it calls any."""

    async def test_seven_tools_and_no_others(self) -> None:
        listed = await self.client.list_tools()
        self.assertEqual({tool.name for tool in listed.tools}, TOOLS)

    async def test_every_one_refuses_arguments_it_does_not_take_in_its_schema(self) -> None:
        listed = await self.client.list_tools()
        for tool in listed.tools:
            with self.subTest(tool=tool.name):
                self.assertIs(tool.input_schema.get("additionalProperties"), False)
                self.assertTrue(tool.description and tool.description.strip(), "a tool with nothing to say for itself")
                self.assertIsNotNone(tool.output_schema, "a result with no schema is a result a model has to guess at")

    async def test_the_choices_are_the_clinics_own_and_not_a_copy(self) -> None:
        tools = {tool.name: tool for tool in (await self.client.list_tools()).tools}

        side = tools["find_slots"].input_schema["properties"]["side"]
        sides = next(option["enum"] for option in side["anyOf"] if "enum" in option)
        self.assertEqual(sorted(sides), sorted(typing.get_args(Side)))

        reason = tools["hand_over"].input_schema["properties"]["reason"]
        self.assertEqual(sorted(reason["enum"]), sorted(typing.get_args(Escalation)))

    async def test_what_only_looks_says_so_and_what_keeps_or_books_does_not(self) -> None:
        tools = {tool.name: tool for tool in (await self.client.list_tools()).tools}
        for name in ("clinic_information", "search_exams", "find_slots"):
            with self.subTest(tool=name):
                self.assertIs(tools[name].annotations.read_only_hint, True)
        for name in ("hold_slot", "confirm_booking", "release_hold", "hand_over"):
            with self.subTest(tool=name):
                self.assertIs(tools[name].annotations.read_only_hint, False)

    async def test_the_server_says_when_to_hand_over_because_it_cannot_hear_the_caller(self) -> None:
        # The rules on chest pain and pacemakers are the reader's, and over
        # this protocol the reader is the model on the other side: the least
        # the server can do is tell it, in the words of the reasons it has.
        instructions = self.client.instructions
        self.assertTrue(instructions, "the server gave no instructions at all")
        for reason in ("needs_a_clinician", "may_be_an_emergency", "already_booked", "asked_for_a_person"):
            with self.subTest(reason=reason):
                self.assertIn(reason, instructions)


class ArgumentsAsTheyArrive(Talking):
    """Anything at all can arrive, and what is wrong with it is said."""

    async def test_an_argument_the_tool_does_not_take_is_refused_by_name(self) -> None:
        # Not dropped: "contrats" dropped is a booking with the contrast left out.
        text = await self.refused(
            "find_slots", saying="takes no argument called contrats", exam="MRI-KNEE", side="left", contrats=False
        )
        self.assertIn("contrast", text)

    async def test_a_side_that_is_not_a_side(self) -> None:
        await self.refused("find_slots", saying="side", exam="MRI-KNEE", side="middle", contrast=False)

    async def test_a_count_that_is_not_a_number_or_is_too_many(self) -> None:
        await self.refused("find_slots", saying="count", exam="XR-CHEST", count="three")
        await self.refused("find_slots", saying="count", exam="XR-CHEST", count=50)

    async def test_a_reason_the_clinic_does_not_have(self) -> None:
        await self.refused("hand_over", saying="reason", reason="gave_up", note="nothing")

    async def test_a_tool_that_is_not_there(self) -> None:
        await self.refused("cancel_booking", saying="Unknown tool")


class TheClinicsRules(Talking):
    """The same rules the conversation keeps, kept here by calling the same code."""

    async def test_an_exam_nobody_offers(self) -> None:
        await self.refused("find_slots", saying="search_exams finds codes", exam="MRI-ELBOW")

    async def test_one_this_agent_may_not_book_is_refused_with_the_reason_and_the_way_on(self) -> None:
        text = await self.refused("find_slots", saying="cannot be booked here", exam="CT-ANGIO")
        self.assertIn("a doctor has to approve the contrast dose first", text)
        self.assertIn("hand_over with reason cannot_be_booked_here", text)

    async def test_a_side_is_asked_for_and_never_guessed(self) -> None:
        await self.refused("find_slots", saying="needs a side", exam="MRI-KNEE", contrast=False)

    async def test_and_so_is_the_contrast(self) -> None:
        await self.refused("find_slots", saying="whether it is with contrast", exam="MRI-SPINE")

    async def test_a_side_for_an_exam_that_has_none_is_refused_rather_than_dropped(self) -> None:
        await self.refused("find_slots", saying="has no side", exam="XR-CHEST", side="left")

    async def test_the_times_are_the_ones_the_conversation_would_read_out(self) -> None:
        found = await self.ok("find_slots", exam="MRI-KNEE", side="left", contrast=False)

        starts = [one["starts"] for one in found["times"]]
        self.assertEqual(starts, ["2026-09-07T09:00:00", "2026-09-07T09:15:00", "2026-09-07T09:30:00"])
        self.assertEqual(found["name"], "MRI knee left without contrast")

        offered = self.clinic.diary.earliest(modality="MR", minutes=30, now=FRIDAY_BEFORE, count=3)
        self.assertEqual(starts, [slot.starts.isoformat() for slot in offered])

    async def test_and_later_ones_when_asked_for_later(self) -> None:
        found = await self.ok("find_slots", exam="XR-CHEST", after="2026-09-07T12:00:00", count=1)
        self.assertEqual(found["times"][0]["starts"], "2026-09-07T12:15:00")

    async def test_a_time_with_a_zone_on_it_is_refused_rather_than_compared(self) -> None:
        await self.refused("find_slots", saying="time zone", exam="XR-CHEST", after="2026-09-07T12:00:00Z")


class KeepingATime(Talking):
    async def test_a_time_is_kept_for_ten_minutes_and_is_not_offered_meanwhile(self) -> None:
        held = await self.kept()

        self.assertEqual(held["until"], "2026-09-04T09:10:00")
        self.assertEqual(held["what"], "MRI knee left without contrast")

        # Half an hour from nine takes a quarter past as well, so the first
        # time still free is half past.
        found = await self.ok("find_slots", exam="MRI-KNEE", side="left", contrast=False, count=1)
        self.assertEqual(found["times"][0]["starts"], "2026-09-07T09:30:00")

    async def test_only_a_time_the_diary_would_offer_can_be_kept(self) -> None:
        # The conversation only ever holds what it was offered. A model can
        # send any room and any minute, and the diary says what is wrong.
        for room, starts, why in (
            ("MR1", "2026-09-07T09:05:00", "every 15 minutes"),
            ("XR1", "2026-09-07T09:00:00", "does not do MR"),
            ("MR1", "2026-09-07T12:45:00", "not open for 30 minutes"),
            ("MR1", "2026-09-06T09:00:00", "not open"),
            ("MR1", "2026-09-03T09:00:00", "already started"),
            ("ZZ9", "2026-09-07T09:00:00", "no room ZZ9"),
        ):
            with self.subTest(room=room, starts=starts):
                text = await self.refused(
                    "hold_slot",
                    saying="not a time the diary offers",
                    exam="MRI-KNEE",
                    side="left",
                    contrast=False,
                    room=room,
                    starts=starts,
                )
                self.assertIn(why, text)

        self.assertEqual(self.clinic.diary.taken(FRIDAY_BEFORE), [], "a refused time was kept anyway")

    async def test_a_time_kept_by_somebody_else_cannot_be_kept_twice(self) -> None:
        await self.kept()
        await self.refused(
            "hold_slot", saying="just been taken", exam="MRI-KNEE", side="left", contrast=False,
            room="MR1", starts="2026-09-07T09:00:00",
        )

    async def test_given_back_it_is_offered_again_and_giving_it_back_twice_is_not_an_error(self) -> None:
        held = await self.kept()

        first = await self.ok("release_hold", hold=held["hold"])
        second = await self.ok("release_hold", hold=held["hold"])

        self.assertTrue(first["released"])
        self.assertFalse(second["released"])
        found = await self.ok("find_slots", exam="MRI-KNEE", side="left", contrast=False, count=1)
        self.assertEqual(found["times"][0]["starts"], "2026-09-07T09:00:00")


class Booking(Talking):
    async def test_a_whole_booking_and_what_the_diary_says_afterwards(self) -> None:
        found = await self.ok("search_exams", phrase="an MRI of my left knee")
        self.assertEqual(found["exams"][0]["code"], "MRI-KNEE")

        times = await self.ok("find_slots", exam="MRI-KNEE", side="left", contrast=False)
        first = times["times"][0]
        held = await self.ok(
            "hold_slot", exam="MRI-KNEE", side="left", contrast=False, room=first["room"], starts=first["starts"]
        )
        booked = await self.ok("confirm_booking", hold=held["hold"], patient="Anna Bianchi")

        self.assertEqual(booked["what"], "MRI knee left without contrast")
        self.assertEqual((booked["side"], booked["contrast"]), ("left", False))

        # And the diary, which is what the people at the desk open on the day.
        kept = self.clinic.diary.booking(booked["reference"])
        assert kept is not None
        self.assertEqual(kept.patient, "Anna Bianchi")
        self.assertEqual([(one.exam.code, one.side, one.contrast) for one in kept.requests], [("MRI-KNEE", "left", False)])
        self.assertEqual(kept.slots[0].starts.isoformat(), first["starts"])

    async def test_a_name_that_is_not_a_name_is_refused_and_the_time_is_still_kept(self) -> None:
        held = await self.kept()

        for patient in ("yes", "RSSMRA85T10A562S"):
            with self.subTest(patient=patient):
                await self.refused("confirm_booking", saying="the time is still being kept", hold=held["hold"], patient=patient)

        booked = await self.ok("confirm_booking", hold=held["hold"], patient="Anna Bianchi")
        self.assertEqual(booked["patient"], "Anna Bianchi")

    async def test_a_hold_this_server_never_made(self) -> None:
        await self.refused("confirm_booking", saying="Call hold_slot first", hold="CDF-HJK", patient="Anna Bianchi")

    async def test_a_hold_that_ran_out(self) -> None:
        held = await self.kept()
        self.clock.now = FRIDAY_BEFORE + timedelta(minutes=11)

        await self.refused("confirm_booking", saying="not kept any more", hold=held["hold"], patient="Anna Bianchi")
        self.assertEqual(self.clinic.diary.bookings(), [])

    async def test_booked_once_and_not_twice(self) -> None:
        held = await self.kept()
        await self.ok("confirm_booking", hold=held["hold"], patient="Anna Bianchi")
        await self.refused("confirm_booking", saying="Call hold_slot first", hold=held["hold"], patient="Anna Bianchi")
        self.assertEqual(len(self.clinic.diary.bookings()), 1)

    async def test_there_is_no_tool_that_changes_or_cancels_a_booking(self) -> None:
        # The conversation hands those calls to a person because nothing here
        # can check who is asking, and a tool for it would be the way round.
        tools = {tool.name for tool in (await self.client.list_tools()).tools}
        for name in tools:
            with self.subTest(tool=name):
                self.assertNotIn("cancel", name)
                self.assertNotIn("move", name)
                self.assertNotIn("reschedule", name)


class FetchingAPerson(Talking):
    async def test_the_reason_and_the_note_are_kept_and_the_time_given_back(self) -> None:
        held = await self.kept()

        handed = await self.ok("hand_over", reason="needs_a_clinician", note="mentioned a pacemaker", hold=held["hold"])

        self.assertEqual(handed["reason"], "needs_a_clinician")
        self.assertEqual(handed["released"], held["hold"])
        self.assertEqual(handed["handed_over_so_far"], 1)
        self.assertEqual(self.clinic.diary.taken(FRIDAY_BEFORE), [])

    async def test_a_handover_with_nothing_to_read_is_refused(self) -> None:
        await self.refused("hand_over", saying="note", reason="asked_for_a_person", note="")


class TheGoldenBookingsThroughThisDoor(unittest.IsolatedAsyncioTestCase):
    """Every booking the golden calls expect, made through the server instead.

    The conversation reached them through sentences; here a client asks for
    the same exam, side, contrast, time and patient by name. What comes out of
    the diary has to be the same booking, read back with the scorer's own
    comparison: two doors, one set of rules, one answer.
    """

    async def test_each_comes_out_of_the_diary_as_the_case_expects(self) -> None:
        golden = load()
        wanted = [case for case in golden.cases if case.expect.tool == "confirm_booking"]
        self.assertGreaterEqual(len(wanted), 40, "fewer golden bookings than there were: this check has stopped reading them")

        for case in wanted:
            with self.subTest(case=case.id):
                booking = case.expect.booking
                assert booking is not None and booking.starts is not None and booking.room is not None
                clinic = default(starting=golden.laid_out_from)

                async with Client(build(clinic, clock=Clock(golden.now)), mode="legacy") as client:
                    arguments = {"exam": booking.exam, "room": booking.room, "starts": booking.starts.isoformat()}
                    if booking.side is not None:
                        arguments["side"] = booking.side
                    if booking.contrast is not None:
                        arguments["contrast"] = booking.contrast

                    held = await client.call_tool("hold_slot", arguments)
                    self.assertFalse(held.is_error, said(held))
                    done = await client.call_tool(
                        "confirm_booking", {"hold": held.structured_content["hold"], "patient": booking.patient}
                    )
                    self.assertFalse(done.is_error, said(done))

                made = clinic.diary.bookings()
                self.assertEqual(len(made), 1)
                self.assertEqual(differences(made[0], booking), [])


class AsAProcess(unittest.IsolatedAsyncioTestCase):
    """``python -m booking_agent.mcp``, started the way a host starts it.

    On its own clock and its own diary, laid out from today, so nothing here
    says which day or time to expect: only what the server said a moment ago.
    """

    def server(self) -> StdioServerParameters:
        return StdioServerParameters(command=sys.executable, args=["-m", "booking_agent.mcp"], cwd=str(HERE))

    async def test_it_starts_lists_its_tools_and_books_down_a_pipe(self) -> None:
        for mode in ("legacy", "auto"):
            with self.subTest(mode=mode):
                async with Client(self.server(), mode=mode) as client:
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    self.assertEqual(tools, TOOLS)

                    found = await client.call_tool("find_slots", {"exam": "XR-CHEST"})
                    self.assertFalse(found.is_error, said(found))
                    first = found.structured_content["times"][0]

                    held = await client.call_tool("hold_slot", {"exam": "XR-CHEST", "room": first["room"], "starts": first["starts"]})
                    self.assertFalse(held.is_error, said(held))

                    booked = await client.call_tool(
                        "confirm_booking", {"hold": held.structured_content["hold"], "patient": "Anna Bianchi"}
                    )
                    self.assertFalse(booked.is_error, said(booked))
                    self.assertEqual(booked.structured_content["starts"], first["starts"])
                    self.assertGreater(datetime.fromisoformat(first["starts"]), datetime.now() - timedelta(minutes=1))

    async def test_and_its_refusals_arrive_as_refusals(self) -> None:
        async with Client(self.server(), mode="legacy") as client:
            refused = await client.call_tool("find_slots", {"exam": "CT-ANGIO"})
            self.assertTrue(refused.is_error)
            self.assertIn("cannot be booked here", said(refused))


class WhatTheResultsLookLikeOnTheWire(Talking):
    async def test_every_result_validates_against_the_schema_it_publishes(self) -> None:
        # Checked by a JSON Schema validator that is not the SDK and not this
        # project: the schema is a promise to whatever reads it, and the
        # promise is worth what an outside reader makes of it.
        import jsonschema

        tools = {tool.name: tool for tool in (await self.client.list_tools()).tools}
        held = await self.kept()

        results = {
            "clinic_information": await self.ok("clinic_information"),
            "search_exams": await self.ok("search_exams", phrase="knee"),
            "find_slots": await self.ok("find_slots", exam="XR-CHEST"),
            "hold_slot": held,
            "confirm_booking": await self.ok("confirm_booking", hold=held["hold"], patient="Anna Bianchi"),
            "release_hold": await self.ok("release_hold", hold="CDF-HJK"),
            "hand_over": await self.ok("hand_over", reason="asked_for_a_person", note="wanted a person"),
        }

        self.assertEqual(set(results), TOOLS)
        for name, result in results.items():
            with self.subTest(tool=name):
                jsonschema.validate(result, tools[name].output_schema)
                json.dumps(result)


if __name__ == "__main__":
    unittest.main()
