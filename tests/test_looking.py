"""The read-only views of the clinic, and the console they feed.

These exist because the agent used to be reachable only through a terminal,
which made it invisible to anybody who was not going to install Python. The
console is the same agent over the same endpoints — so what matters here is
that these views tell the truth about the clinic the agent is actually
booking in, and that nothing among them can book anything.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from fastapi.testclient import TestClient

from booking_agent.clinic.build import default
from booking_agent.service.api import build


class LookingAtTheClinic(unittest.TestCase):
    """What a screen is told about the clinic."""

    def setUp(self) -> None:
        self.clinic = default()

        # A fixed moment, and a Monday morning. A test that asks a diary what
        # is free "now" is a test that passes on the day it was written and
        # fails on a Sunday.
        self.now = datetime(2026, 9, 7, 8, 0)
        self.client = TestClient(build(self.clinic, clock=lambda: self.now))

    def test_says_who_the_clinic_is_in_the_agents_own_words(self) -> None:
        said = self.client.get("/clinic").json()

        self.assertEqual(said["name"], self.clinic.name)
        self.assertEqual(said["opening_hours"], self.clinic.opening_hours)
        self.assertEqual(said["exams"], len(self.clinic.catalogue))
        self.assertTrue(said["rooms"], "a clinic with no rooms cannot be booked in")

    def test_the_catalogue_lists_everything_including_what_cannot_be_booked(self) -> None:
        said = self.client.get("/catalogue").json()

        self.assertEqual(len(said["exams"]), len(self.clinic.catalogue))

        # An exam the agent may not book is listed, with the reason. Hiding it
        # would make it look like something the clinic does not do, when it is
        # something a person has to arrange.
        refused = [one for one in said["exams"] if not one["bookable"]]
        self.assertTrue(refused, "the invented clinic is meant to have one of these")
        for one in refused:
            self.assertTrue(one["unbookable_reason"], f"{one['code']} is refused without saying why")

    def test_searching_it_is_the_agents_own_search(self) -> None:
        """The screen must not have a second search of its own."""
        said = self.client.get("/catalogue", params={"q": "knee"}).json()

        found = {one["code"] for one in said["exams"]}
        wanted = {match.exam.code for match in self.clinic.catalogue.search("knee", limit=20)}

        self.assertEqual(found, wanted)
        self.assertTrue(said["searched"])

        # And the ambiguity survives, because it is the reason the agent asks
        # "did you mean": two exams answer to the one word.
        self.assertGreaterEqual(len(found), 2, "the invented catalogue is meant to be ambiguous here")

    def test_and_says_which_words_it_was_found_on(self) -> None:
        said = self.client.get("/catalogue", params={"q": "knee"}).json()

        for one in said["exams"]:
            self.assertIn("knee", one["matched"], f"{one['code']} was returned without saying why")

    def test_the_diary_is_free_for_a_length_rather_than_free(self) -> None:
        """Thirty minutes free does not mean forty-five fits."""
        short = self.client.get("/diary", params={"days": 3, "minutes": 15}).json()
        long = self.client.get("/diary", params={"days": 3, "minutes": 120}).json()

        def slots(said) -> int:
            return sum(len(room["free"]) + room["more"] for day in said["days"] for room in day["rooms"])

        self.assertEqual(short["for_minutes"], 15)
        self.assertEqual(long["for_minutes"], 120)
        self.assertGreater(slots(short), slots(long), "a longer exam cannot fit in more places than a shorter one")

    def test_the_diary_refuses_a_silly_number_of_days_rather_than_working_for_ever(self) -> None:
        said = self.client.get("/diary", params={"days": 9000, "minutes": 30}).json()
        self.assertLessEqual(len(said["days"]), 14)

    def test_a_booking_stops_the_time_being_offered(self) -> None:
        """The whole reason the diary is on the screen beside the conversation."""
        before = self.client.get("/diary", params={"days": 3, "minutes": 30}).json()
        free_before = {
            (room["room"], at) for day in before["days"] for room in day["rooms"] for at in room["free"]
        }
        self.assertTrue(free_before)

        call = self.client.post("/calls", json={"channel": "chat"}).json()["call"]
        for words in ["MRI knee", "left, no contrast", "Anna Bianchi"]:
            self.client.post(f"/calls/{call}/said", json={"text": words})

        # Offered, and then taken.
        said = self.client.post(f"/calls/{call}/said", json={"text": "the first"}).json()
        self.assertEqual(said["stage"], "confirming")
        done = self.client.post(f"/calls/{call}/said", json={"text": "yes"}).json()
        self.assertIsNotNone(done["booking"], "the conversation did not reach a booking")

        booked = self.client.get("/bookings").json()["bookings"]
        self.assertEqual(len(booked), 1)
        self.assertEqual(booked[0]["reference"], done["booking"]["reference"])

        after = self.client.get("/diary", params={"days": 3, "minutes": 30}).json()
        free_after = {
            (room["room"], at) for day in after["days"] for room in day["rooms"] for at in room["free"]
        }

        self.assertLess(len(free_after), len(free_before), "booking something freed nothing up, which is impossible")

        taken = {(one["room"], one["starts"]) for one in after["not_free"]}
        self.assertIn(
            (booked[0]["room"], booked[0]["starts"]),
            taken,
            "the booked time is not among the times the diary calls taken",
        )

    def test_nothing_among_the_views_can_change_anything(self) -> None:
        """A screen with its own path to the diary is the path nobody tested."""
        for where in ("/clinic", "/catalogue", "/diary", "/bookings"):
            for how in ("post", "put", "patch", "delete"):
                response = getattr(self.client, how)(where)
                self.assertIn(
                    response.status_code,
                    (404, 405),
                    f"{how.upper()} {where} is not read-only",
                )


class TheConsole(unittest.TestCase):
    """That the page is served at all, and served before the API."""

    def setUp(self) -> None:
        self.client = TestClient(build(default(), clock=lambda: datetime(2026, 9, 7, 8, 0)))

    def test_the_page_is_served_at_the_root(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Talk to it", response.text)

    def test_and_the_api_still_wins_over_it(self) -> None:
        """A static mount at "/" placed first swallows every route above it."""
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/clinic").status_code, 200)

    def test_the_mark_is_the_same_drawing_in_both_places(self) -> None:
        """Two files, one drawing, and remembering is not a plan.

        A favicon is loaded outside the page and inherits none of its colours,
        so the two cannot be one file. What they can be is checked.
        """
        import re
        from pathlib import Path

        web = Path(__file__).resolve().parent.parent / "web"
        page = (web / "index.html").read_text(encoding="utf-8")
        icon = (web / "mark.svg").read_text(encoding="utf-8")

        marked = re.search(r"<svg\b[^>]*\bdata-mark\b[\s\S]*?</svg>", page)
        self.assertIsNotNone(marked, "no <svg data-mark> in the page, so there is nothing to compare")

        def shapes(source: str) -> list[tuple]:
            return [
                (
                    one.group(1),
                    _number(one.group(2), "x"),
                    _number(one.group(2), "y"),
                    _number(one.group(2), "width"),
                    _number(one.group(2), "height"),
                )
                for one in re.finditer(r"<(rect)\b([^>]*)>", source)
            ]

        drawn = shapes(marked.group(0))
        self.assertTrue(drawn, "the mark has no shapes in it")
        self.assertEqual(drawn, shapes(icon), "the header mark and the tab icon have drifted apart")


def _number(attributes: str, name: str) -> float:
    found = __import__("re").search(rf'\b{name}="([\d.]+)"', attributes)
    return float(found.group(1)) if found else 0.0


if __name__ == "__main__":
    unittest.main()


class WhatTheButtonsPromise(unittest.TestCase):
    """Every "try this" button, said to the real agent.

    The page offers a handful of sentences with a label on each one saying what
    it demonstrates. That is a promise, and a promise on a page is worth exactly
    as much as the check behind it.

    This is not hypothetical. The first version of that list had a button
    labelled "something it must not answer" whose sentence the agent answered
    quite happily — it has no rule about clinical questions and never claimed
    one — and another naming an exam this clinic does not have. Both looked
    entirely convincing until somebody pressed them.
    """

    def setUp(self) -> None:
        self.client = TestClient(build(default(), clock=lambda: datetime(2026, 9, 7, 8, 0)))

    def buttons(self) -> list[tuple[str, str, str]]:
        import re
        from pathlib import Path

        page = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")

        found = [
            (one.group(1), one.group(2), one.group(3).strip())
            for one in re.finditer(
                r'<button[^>]*data-say="([^"]+)"[^>]*data-expect="([^"]+)"[^>]*>([^<]+)</button>', page
            )
        ]

        # A pattern that stops matching does not fail, it reports success over
        # an empty list. The page has five of these and will not have fewer.
        self.assertGreaterEqual(len(found), 4, "no try-this buttons found: this check has stopped reading the page")
        return found

    def test_every_one_of_them_does_what_its_label_says(self) -> None:
        for says, expected, label in self.buttons():
            with self.subTest(button=label):
                call = self.client.post("/calls", json={"channel": "chat"}).json()["call"]
                said = self.client.post(f"/calls/{call}/said", json={"text": says}).json()

                if expected == "handover":
                    self.assertIsNotNone(
                        said["handed_over"],
                        f'"{label}" says {says!r} and the agent carried on: {said["reply"]!r}',
                    )
                elif expected == "asks-back":
                    self.assertIsNone(
                        said["handed_over"],
                        f'"{label}" says {says!r} and the agent handed over instead of asking',
                    )
                    self.assertTrue(said["reply"].strip(), "the agent said nothing at all")
                    self.assertFalse(said["over"], "a first sentence should not end the call")
                else:
                    self.fail(f'"{label}" expects {expected!r}, which this check does not know how to verify')

    def test_and_each_one_says_why_it_handed_over(self) -> None:
        """A handover with no reason is a dead end rather than a handover."""
        for says, expected, label in self.buttons():
            if expected != "handover":
                continue

            with self.subTest(button=label):
                call = self.client.post("/calls", json={"channel": "chat"}).json()["call"]
                said = self.client.post(f"/calls/{call}/said", json={"text": says}).json()

                self.assertIn(
                    said["handed_over"],
                    (
                        "asked_for_a_person",
                        "not_understood",
                        "cannot_be_booked_here",
                        "booking_failed",
                        "caller_is_upset",
                        "already_booked",
                    ),
                    f'"{label}" handed over for a reason nothing knows about',
                )
