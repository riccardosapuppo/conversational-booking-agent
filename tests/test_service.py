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

        self.assertEqual(said["clinic"], "Kesterby Diagnostic Centre")
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
        # Which knee, and whether contrast: the two things a code cannot say.
        self.assertEqual(done["booking"]["what"], ["MRI knee left without contrast"])
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


def imported(source: str) -> set[str]:
    """Every module a source file imports, by its full dotted name.

    Read off the syntax tree rather than the text. The text version looked for
    the word "fastapi" anywhere in a file, so a comment explaining that the
    domain does not use it counted as using it, and the word "mcp" is in every
    sentence that says the domain does not know about the server. What a file
    imports is what its import statements say.
    """
    import ast

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def reaches(name: str, roots: tuple[str, ...]) -> bool:
    return any(name == root or name.startswith(root + ".") for root in roots)


#: What the domain must never know is there: a web server, a protocol, the
#: client that talks to a switchboard, the doors built on them, or the reader
#: that asks a model over the network.
NOT_IN_THE_DOMAIN = (
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "mcp",
    "mcp_types",
    "booking_agent.service",
    "booking_agent.telephony",
    "booking_agent.mcp",
    "booking_agent.model",
)


class WhatTheServiceDoesNotKnow(unittest.TestCase):
    here = __import__("pathlib").Path(__file__).resolve().parents[1] / "booking_agent"

    def modules(self, package: str) -> list:
        found = sorted((self.here / package).glob("*.py"))
        # A package with no modules in it is a check of nothing.
        self.assertTrue(found, f"booking_agent/{package} has no modules, so there is nothing to check")
        return found

    def test_nothing_below_it_imports_it(self) -> None:
        # The direction of the dependency is the whole design, and it is the
        # kind of thing that gets broken by one convenient import. The domain
        # must not know it is behind a web server, or behind a protocol for
        # models: both are doors, and the domain is the room.
        for package in ("clinic", "conversation", "channels"):
            for module in self.modules(package):
                for name in imported(module.read_text(encoding="utf-8")):
                    with self.subTest(module=f"{package}/{module.name}", imports=name):
                        self.assertFalse(reaches(name, NOT_IN_THE_DOMAIN), f"{package}/{module.name} imports {name}")

    def test_the_server_for_models_is_beside_the_web_server_and_not_on_it(self) -> None:
        for module in self.modules("mcp"):
            for name in imported(module.read_text(encoding="utf-8")):
                with self.subTest(module=module.name, imports=name):
                    self.assertFalse(
                        reaches(name, ("fastapi", "uvicorn", "booking_agent.service", "booking_agent.telephony")),
                        f"mcp/{module.name} imports {name}",
                    )

    def test_the_reader_that_asks_a_model_stands_on_nothing_but_the_domain(self) -> None:
        # It talks to Ollama through the standard library, so choosing it
        # installs nothing, and it is a reader: it has no business with the
        # web server, the switchboard or the protocol for models.
        for module in self.modules("model"):
            for name in imported(module.read_text(encoding="utf-8")):
                with self.subTest(module=module.name, imports=name):
                    self.assertFalse(
                        reaches(name, ("fastapi", "uvicorn", "httpx", "mcp", "booking_agent.service",
                                       "booking_agent.telephony", "booking_agent.mcp")),
                        f"model/{module.name} imports {name}",
                    )

    def test_and_neither_of_the_other_doors_knows_it_is_there(self) -> None:
        for package in ("service", "telephony"):
            for module in self.modules(package):
                for name in imported(module.read_text(encoding="utf-8")):
                    with self.subTest(module=f"{package}/{module.name}", imports=name):
                        self.assertFalse(reaches(name, ("mcp", "booking_agent.mcp")), f"{package}/{module.name} imports {name}")

    def test_the_check_finds_the_imports_it_is_there_to_find(self) -> None:
        # Planted, because a check that passes by finding nothing has to be
        # shown finding something: these four are what it exists to refuse,
        # and the docstring that names them is not an import of any of them.
        planted = (
            '"""Nothing here uses fastapi or mcp."""\n'
            "import mcp\n"
            "import mcp.types as kinds\n"
            "from fastapi import FastAPI\n"
            "from booking_agent.mcp.server import build\n"
        )
        names = imported(planted)

        self.assertEqual(names, {"mcp", "mcp.types", "fastapi", "booking_agent.mcp.server"})
        self.assertTrue(all(reaches(name, NOT_IN_THE_DOMAIN) for name in names))
        self.assertFalse(reaches("mcpx", NOT_IN_THE_DOMAIN), "a module merely starting with mcp is not mcp")


if __name__ == "__main__":
    unittest.main()
