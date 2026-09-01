"""The service, through a real client against the real application.

Not by calling the handler functions. Half of what a web service gets wrong
lives in the layer that skipping them skips: the status code, the shape of the
body, what happens to a field nobody filled in, what a client sees when it asks
for something that is not there.

The clock is handed in, so a call that was an hour ago is one line rather than
an hour.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from booking_agent.clinic.build import default
from booking_agent.service.api import build

MONDAY = date(2026, 9, 7)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def forward(self, **by) -> None:
        self.now += timedelta(**by)


def service(clock: Clock | None = None) -> tuple[TestClient, Clock]:
    hands = clock or Clock(datetime(2026, 9, 4, 9, 0))
    return TestClient(build(default(starting=MONDAY), clock=hands)), hands


class BeforeAnybodyRings(unittest.TestCase):
    def test_it_says_what_it_is_answering_for(self) -> None:
        client, _ = service()

        said = client.get("/health").json()

        self.assertEqual(said["clinic"], "Example Clinic")
        self.assertGreater(said["exams"], 0)
        self.assertEqual(said["calls"], 0)


class AWholeCall(unittest.TestCase):
    def test_start_to_reference(self) -> None:
        client, _ = service()

        started = client.post("/calls", json={"channel": "chat"})
        self.assertEqual(started.status_code, 201)
        call = started.json()["call"]
        self.assertIn("book", started.json()["reply"].lower())

        for text in ("mri knee", "left", "no contrast", "Mario Rossi", "1"):
            answered = client.post(f"/calls/{call}/said", json={"text": text})
            self.assertEqual(answered.status_code, 200)
            self.assertFalse(answered.json()["over"])

        done = client.post(f"/calls/{call}/said", json={"text": "yes"}).json()

        self.assertTrue(done["over"])
        self.assertEqual(done["stage"], "booked")
        self.assertEqual(done["booking"]["patient"], "Mario Rossi")
        self.assertEqual(done["booking"]["exams"], ["MRI-KNEE"])
        self.assertIn(done["booking"]["reference"], done["reply"])

    def test_asking_where_it_has_got_to_does_not_move_it_on(self) -> None:
        client, _ = service()
        call = client.post("/calls", json={}).json()["call"]

        client.post(f"/calls/{call}/said", json={"text": "mri knee"})
        looked = client.get(f"/calls/{call}").json()
        again = client.get(f"/calls/{call}").json()

        self.assertEqual(looked["reply"], again["reply"])
        self.assertIn("left or right", looked["reply"].lower())

    def test_hanging_up(self) -> None:
        client, _ = service()
        call = client.post("/calls", json={}).json()["call"]

        self.assertEqual(client.delete(f"/calls/{call}").status_code, 204)
        self.assertEqual(client.get(f"/calls/{call}").status_code, 404)
        self.assertEqual(client.delete(f"/calls/{call}").status_code, 404)


class OnTheTelephone(unittest.TestCase):
    def test_the_channel_is_chosen_once_and_holds(self) -> None:
        client, _ = service()
        call = client.post("/calls", json={"channel": "voice"}).json()["call"]

        for text in ("mri knee", "left", "no contrast", "Mario Rossi"):
            said = client.post(f"/calls/{call}/said", json={"text": text}).json()["reply"]

        # Four turns later it is still being spoken to, not written at.
        self.assertIn("The first is", said)
        self.assertNotIn("1)", said)
        self.assertIn("in the morning", said)


class WhenSomethingIsWrong(unittest.TestCase):
    def test_a_call_that_was_never_started(self) -> None:
        client, _ = service()

        answered = client.post("/calls/nothing/said", json={"text": "hello"})

        self.assertEqual(answered.status_code, 404)

    def test_a_call_nobody_has_spoken_to_for_an_hour(self) -> None:
        client, clock = service()
        call = client.post("/calls", json={}).json()["call"]

        clock.forward(hours=2)

        self.assertEqual(client.get(f"/calls/{call}").status_code, 404)

    def test_but_a_call_being_used_is_not_forgotten(self) -> None:
        client, clock = service()
        call = client.post("/calls", json={}).json()["call"]

        for _ in range(4):
            clock.forward(minutes=50)
            self.assertEqual(
                client.post(f"/calls/{call}/said", json={"text": "hello"}).status_code, 200
            )

    def test_saying_something_after_it_has_finished(self) -> None:
        client, _ = service()
        call = client.post("/calls", json={}).json()["call"]
        client.post(f"/calls/{call}/said", json={"text": "put me through to a person"})

        after = client.post(f"/calls/{call}/said", json={"text": "actually, wait"})

        self.assertEqual(after.status_code, 409)
        self.assertIn("handed_over", after.json()["detail"])

    def test_an_empty_message_is_refused_rather_than_guessed_at(self) -> None:
        client, _ = service()
        call = client.post("/calls", json={}).json()["call"]

        self.assertEqual(
            client.post(f"/calls/{call}/said", json={"text": ""}).status_code, 422
        )

    def test_and_so_is_one_that_is_not_a_sentence(self) -> None:
        # A caller can say a lot. A megabyte is not somebody talking.
        client, _ = service()
        call = client.post("/calls", json={}).json()["call"]

        answered = client.post(f"/calls/{call}/said", json={"text": "a" * 5000})

        self.assertEqual(answered.status_code, 422)

    def test_starting_a_call_tidies_the_ones_that_are_over(self) -> None:
        client, clock = service()
        for _ in range(3):
            client.post("/calls", json={})

        self.assertEqual(client.get("/health").json()["calls"], 3)

        clock.forward(hours=2)
        client.post("/calls", json={})

        self.assertEqual(client.get("/health").json()["calls"], 1)


class WhatTheServiceDoesNotKnow(unittest.TestCase):
    def test_nothing_below_it_imports_it(self) -> None:
        # The direction of the dependency is the whole design, and it is the
        # kind of thing that gets broken by one convenient import. The domain
        # must not know it is behind a web server.
        from pathlib import Path

        here = Path(__file__).resolve().parents[1] / "booking_agent"
        for package in ("clinic", "conversation", "channels"):
            for module in (here / package).glob("*.py"):
                with self.subTest(module=f"{package}/{module.name}"):
                    source = module.read_text(encoding="utf-8")
                    self.assertNotIn("booking_agent.service", source)
                    self.assertNotIn("fastapi", source)


if __name__ == "__main__":
    unittest.main()
