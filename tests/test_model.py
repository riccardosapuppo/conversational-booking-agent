"""The reader that asks a model, checked without one.

Nothing here talks to a model. The reader is handed its answers: by a function,
to check what it asks and what it makes of what it is told; by an Ollama that is
not one, listening on this machine, for the HTTP it speaks; and by the recording
of a run on a real model, which puts the hundred golden calls through it again
and fails unless every verdict comes out as it did on the day. CI has no model
to run, and this is what it runs instead.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import unittest
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from booking_agent.clinic.build import default
from booking_agent.clinic.catalogue import Catalogue, Exam
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Reading
from booking_agent.conversation.state import new
from booking_agent.model import reader as model
from booking_agent.model.reader import ANSWERS, OPTIONS, QUESTIONS, Model, ModelError, describe, key, ollama, resident

HERE = Path(__file__).resolve().parents[1]
#: Every recorded run, and not the sentences the prompt was written against,
#: which share the folder.
RUNS = sorted(path for path in (HERE / "evals" / "models").glob("*.json") if path.name != "development.json")

#: The runs that can be replayed. The first was recorded with one answer per
#: request and says so in the file; it is kept as it was measured.
RECORDINGS = [path for path in RUNS if json.loads(path.read_text(encoding="utf-8")).get("replayable", True)]


def catalogue() -> Catalogue:
    return default(starting=date(2026, 9, 7)).catalogue


def answer(**fields) -> dict:
    """What Ollama sends back, with these fields in the JSON and the rest null."""
    written = {name: None for name in ("concern", "exam", "exam_words", "side", "contrast", "which", "name")}
    written.update(intent="unclear")
    written.update(fields)
    return {"model": "qwen2.5:3b", "message": {"role": "assistant", "content": json.dumps(written)}, "done": True}


class Asked:
    """A send that keeps what it was asked and answers as it is told."""

    def __init__(self, reply: dict | None = None) -> None:
        self.reply = reply or answer()
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        return self.reply


class WhatItAsks(unittest.TestCase):
    def test_the_same_message_and_question_are_the_same_request(self) -> None:
        # Which is what lets a recording stand in for the model: an answer is
        # looked up by the request, so the request has to be the same each time.
        asked = Asked()
        reading = Model(catalogue(), send=asked)

        reading.read("the left knee please", expecting="side")
        reading.read("the left knee please", expecting="side")
        reading.read("the left knee please", expecting="")

        self.assertEqual(key(asked.requests[0]), key(asked.requests[1]))
        self.assertNotEqual(key(asked.requests[0]), key(asked.requests[2]))

    def test_at_temperature_zero_with_a_seed_and_no_streaming(self) -> None:
        asked = Asked()
        Model(catalogue(), model="qwen2.5:3b", send=asked).read("hello")

        request = asked.requests[0]
        self.assertEqual(request["model"], "qwen2.5:3b")
        self.assertEqual(request["options"], {"temperature": 0, "seed": 7, "num_ctx": 2048})
        self.assertIs(request["stream"], False)

    def test_the_instructions_are_the_clinic_it_reads_for(self) -> None:
        asked = Asked()
        Model(catalogue(), send=asked).read("hello")

        system = asked.requests[0]["messages"][0]["content"]
        for exam in catalogue():
            with self.subTest(exam=exam.code):
                self.assertIn(f"{exam.code}: {exam.name}", system)
        self.assertIn("CT-ANGIO: CT angiography (also angiography, angio); cannot be booked on this line", system)

    def test_and_the_message_comes_with_the_question_it_answers(self) -> None:
        for expecting, (question, _) in QUESTIONS.items():
            with self.subTest(expecting=expecting):
                asked = Asked()
                Model(catalogue(), send=asked).read("something", expecting=expecting)
                said = asked.requests[0]["messages"][1]["content"]
                self.assertIn(question, said)
                self.assertTrue(said.endswith("The caller said: something"))

    def test_a_question_it_has_no_words_for_is_an_error(self) -> None:
        with self.assertRaises(ModelError):
            Model(catalogue(), send=Asked()).read("left", expecting="the weather")

    def test_the_schema_has_only_the_clinics_exams_in_it(self) -> None:
        asked = Asked()
        Model(catalogue(), send=asked).read("hello")

        exams = asked.requests[0]["format"]["properties"]["exam"]["anyOf"][0]["enum"]
        self.assertEqual(exams, [exam.code for exam in catalogue()])

    def test_and_choosing_only_where_there_was_something_to_choose_from(self) -> None:
        # The commonest wrong answer while the prompt was written: a side said
        # on its own, "the left one", taken for picking an option.
        for expecting in QUESTIONS:
            with self.subTest(expecting=expecting):
                asked = Asked()
                Model(catalogue(), send=asked).read("the left one", expecting=expecting)
                intents = asked.requests[0]["format"]["properties"]["intent"]["enum"]
                self.assertEqual("choose" in intents, expecting in ("slot", "exam_choice"))
                for always in ("urgent", "clinical", "operator", "unclear"):
                    self.assertIn(always, intents)

    def test_the_options_and_the_answers_are_the_ones_described(self) -> None:
        self.assertEqual(set(ANSWERS), set(QUESTIONS))
        self.assertEqual(OPTIONS["temperature"], 0)


class WhatItMakesOfTheAnswer(unittest.TestCase):
    def read(self, text: str, expecting: str = "", **fields) -> Reading:
        return Model(catalogue(), send=Asked(answer(**fields))).read(text, expecting=expecting)

    def test_an_exam_is_handed_on_by_its_name_in_the_catalogue(self) -> None:
        reading = self.read("XR knee rt", intent="book", exam="XR-KNEE", side="right")

        self.assertEqual(reading.exam_text, "X-ray knee")
        self.assertEqual(reading.side, "right")
        request = catalogue().resolve(reading.exam_text)
        assert request is not None
        self.assertEqual(request.exam.code, "XR-KNEE")

    def test_and_every_name_finds_its_own_exam_and_no_other(self) -> None:
        # So the exam the model names is the exam the graph books.
        for exam in catalogue():
            with self.subTest(exam=exam.code):
                found = catalogue().resolve(exam.name)
                assert found is not None
                self.assertEqual(found.exam, exam)

    def test_a_catalogue_where_one_does_not_is_refused(self) -> None:
        twins = Catalogue(
            [
                Exam(code="A", name="MRI knee", modality="MR", minutes=30, price=1.0),
                Exam(code="B", name="MRI knee arthrogram", modality="MR", minutes=45, price=2.0),
                Exam(code="C", name="Knee MRI arthrogram", modality="MR", minutes=45, price=2.0),
            ]
        )
        with self.assertRaises(ModelError):
            Model(twins, send=Asked())

    def test_words_that_fit_two_exams_are_left_for_the_catalogue_to_ask_about(self) -> None:
        reading = self.read("my knee", intent="book", exam_words="knee")

        self.assertEqual(reading.exam_text, "knee")
        self.assertIsNone(catalogue().resolve(reading.exam_text))

    def test_a_name_only_when_a_name_was_asked_for(self) -> None:
        self.assertEqual(self.read("Paola Moretti", "patient", intent="book", name="Paola Moretti").name, "Paola Moretti")
        self.assertEqual(self.read("Paola Moretti", "", intent="book", name="Paola Moretti").name, "")

    def test_which_only_when_one_was_picked(self) -> None:
        self.assertEqual(self.read("the middle one", "slot", intent="choose", which=2).which, 2)
        self.assertIsNone(self.read("yes", "confirm", intent="agree", which=2).which)

    def test_and_the_words_that_made_it_urgent(self) -> None:
        reading = self.read("I fell and my wrist looks wrong", intent="urgent", concern="my wrist looks wrong")

        self.assertEqual((reading.intent, reading.concern), ("urgent", "my wrist looks wrong"))

    def test_nothing_said_is_not_worth_asking_about(self) -> None:
        asked = Asked()
        self.assertEqual(Model(catalogue(), send=asked).read("   ").intent, "unclear")
        self.assertEqual(asked.requests, [])


class WhatItRefuses(unittest.TestCase):
    """Answers no schema allows, which a recording edited by hand or another server could still bring."""

    def refused(self, reply: dict, expecting: str = "") -> None:
        with self.assertRaises(ModelError):
            Model(catalogue(), send=Asked(reply)).read("something", expecting=expecting)

    def test_what_is_not_the_json_asked_for(self) -> None:
        self.refused({"message": {"content": "Sure! Here is the booking you asked for."}})
        self.refused({"error": "model not found"})

    def test_a_field_missing_or_one_too_many(self) -> None:
        good = json.loads(answer()["message"]["content"])
        del good["side"]
        self.refused({"message": {"content": json.dumps(good)}})
        self.refused(answer(colour="blue"))

    def test_an_intent_the_question_does_not_allow(self) -> None:
        self.refused(answer(intent="choose", which=1), expecting="side")
        self.refused(answer(intent="cancel"))

    def test_an_exam_the_clinic_does_not_have(self) -> None:
        self.refused(answer(intent="book", exam="CT-HEAD"))

    def test_values_of_the_wrong_kind(self) -> None:
        self.refused(answer(intent="book", side="middle"))
        self.refused(answer(intent="book", contrast="yes"))
        self.refused(answer(intent="choose", which="2"), expecting="slot")
        self.refused(answer(intent="choose", which=True), expecting="slot")
        self.refused(answer(intent="book", name=7), expecting="patient")


def serving(test: unittest.TestCase, reply) -> tuple[str, list]:
    """An Ollama that is not one, on this machine, for as long as the test runs."""
    asked: list = []

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload) -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            asked.append((self.path, body))
            self._send(*reply(self.path, body))

        def do_GET(self) -> None:
            asked.append((self.path, None))
            self._send(*reply(self.path, None))

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_address[1]}", asked


def nothing_listening() -> str:
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    return f"http://127.0.0.1:{port}"


class OverANetworkThatIsSimulated(unittest.TestCase):
    def test_it_posts_to_the_chat_endpoint_and_reads_what_comes_back(self) -> None:
        url, asked = serving(self, lambda path, body: (200, answer(intent="book", exam="MAM-SCREEN")))

        reading = Model(catalogue(), send=ollama(url)).read("a mammogram, please")

        self.assertEqual(reading.exam_text, "Mammogram")
        path, body = asked[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["format"]["required"][0], "intent")
        self.assertEqual(body["messages"][1]["content"].splitlines()[-1], "The caller said: a mammogram, please")

    def test_an_error_from_the_server_is_an_error_with_what_it_said(self) -> None:
        url, _ = serving(self, lambda path, body: (500, {"error": "out of memory"}))

        with self.assertRaises(ModelError) as raised:
            Model(catalogue(), send=ollama(url)).read("hello")
        self.assertIn("500", str(raised.exception))
        self.assertIn("out of memory", str(raised.exception))

    def test_nothing_listening_is_an_error_that_says_what_to_start(self) -> None:
        with self.assertRaises(ModelError) as raised:
            Model(catalogue(), send=ollama(nothing_listening(), timeout=5)).read("hello")
        self.assertIn("is Ollama running?", str(raised.exception))

    def test_which_weights_are_asked_of_the_server_and_not_remembered(self) -> None:
        def reply(path: str, body):
            if path == "/api/version":
                return 200, {"version": "0.40.0"}
            if path == "/api/tags":
                return 200, {
                    "models": [
                        {
                            "name": "qwen2.5:3b",
                            "digest": "357c53fb",
                            "size": 1929912432,
                            "details": {"parameter_size": "3.1B", "quantization_level": "Q4_K_M"},
                        }
                    ]
                }
            return 200, {"models": [{"name": "qwen2.5:3b", "size": 2600000000, "size_vram": 0}]}

        url, _ = serving(self, reply)

        self.assertEqual(
            describe("qwen2.5:3b", url),
            {
                "ollama": "0.40.0",
                "model": "qwen2.5:3b",
                "digest": "357c53fb",
                "bytes": 1929912432,
                "parameters": "3.1B",
                "quantization": "Q4_K_M",
            },
        )
        self.assertEqual(resident("qwen2.5:3b", url), {"bytes": 2600000000, "in_vram_bytes": 0})
        with self.assertRaises(ModelError) as raised:
            describe("llama3.2:3b", url)
        self.assertIn("ollama pull llama3.2:3b", str(raised.exception))


def _words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


class ThePromptIsNotTheTest(unittest.TestCase):
    def test_no_golden_line_is_in_the_words_written_for_the_model(self) -> None:
        """What was written by hand for the model, against what the golden calls say.

        The prompt was written against sentences of its own, and a golden line
        copied into it, even as an example, would be the model being handed the
        answer to a case it is then measured on. Lines of three words or more:
        "yes" and "left" are in any prompt about bookings. The catalogue is not
        checked, because it is the clinic's own data and the calls name it.
        """
        written = _words(model._INSTRUCTIONS.replace("{exams}", " ") + " " + " ".join(q + " " + h for q, h in QUESTIONS.values()))
        golden = json.loads((HERE / "evals" / "golden.json").read_text(encoding="utf-8"))
        lines = {_words(line) for case in golden["cases"] for line in case["said"]}

        found = sorted(line for line in lines if len(line.split()) >= 3 and f" {line} " in f" {written} ")
        self.assertEqual(found, [])

    def test_nor_is_any_of_the_sentences_it_was_written_against(self) -> None:
        # Of any length this time: these were written to correct the prompt
        # on, and one of them being a golden line would make it a case the
        # prompt was corrected against. One was, "the first", and was changed.
        sentences = json.loads((HERE / "evals" / "models" / "development.json").read_text(encoding="utf-8"))["sentences"]
        golden = json.loads((HERE / "evals" / "golden.json").read_text(encoding="utf-8"))
        lines = {_words(line) for case in golden["cases"] for line in case["said"]}

        self.assertEqual(sorted(one["said"] for one in sentences if _words(one["said"]) in lines), [])
        for one in sentences:
            with self.subTest(said=one["said"]):
                self.assertIn(one["expecting"], QUESTIONS)
                self.assertLessEqual(set(one["reads"]), {"intent", "exam", "side", "contrast", "which", "name"})


class TheGraphKeepsWhatTheReaderRead(unittest.TestCase):
    def test_a_side_and_a_contrast_said_with_the_exam_named_in_the_catalogues_words(self) -> None:
        # The graph used to read them out of the exam's words, as the rules
        # do, and "MRI knee" has neither: the caller was asked for the side
        # they had just given.
        class Named:
            def read(self, text: str, *, expecting: str = "") -> Reading:
                return Reading(intent="book", exam_text="MRI knee", side="right", contrast=False)

        clinic = default(starting=date(2026, 9, 7))
        one = Agent(catalogue=clinic.catalogue, diary=clinic.diary, reader=Named())
        talk = new("named")

        said = one.reply_to(talk, "a resonance of the right one, no dye", now=datetime(2026, 9, 4, 9, 0))

        self.assertEqual(talk.requests[0].description, "MRI knee right without contrast")
        self.assertNotIn("Left or right?", said)

    def test_an_exam_called_by_its_name_is_that_exam_whatever_was_just_asked(self) -> None:
        # Found by a review: asked "MRI knee or X-ray knee?", "neither, a
        # chest film", read as a refusal naming the chest, was looked for
        # among the two knees, and "X-ray chest" found the X-ray of the knee.
        class Asked:
            def read(self, text: str, *, expecting: str = "") -> Reading:
                if text == "knee":
                    return Reading(intent="book", exam_text="knee")
                return Reading(intent="refuse", exam_text="X-ray chest", side="right")

        clinic = default(starting=date(2026, 9, 7))
        one = Agent(catalogue=clinic.catalogue, diary=clinic.diary, reader=Asked())
        talk = new("chest")

        self.assertIn("Did you mean", one.reply_to(talk, "knee", now=datetime(2026, 9, 4, 9, 0)))
        one.reply_to(talk, "neither, a chest film", now=datetime(2026, 9, 4, 9, 0))

        self.assertEqual([request.exam.code for request in talk.requests], ["XR-CHEST"])


class InTheConsole(unittest.TestCase):
    def test_the_service_says_which_reader_it_reads_with(self) -> None:
        # Read off the object, as for the rules: the page names the class in
        # use and the file it is in, with no line of the page changed.
        from fastapi.testclient import TestClient

        from booking_agent.service.api import build

        clinic = default(starting=date(2026, 9, 7))
        reading = TestClient(build(clinic, reader=Model(clinic.catalogue, send=Asked()))).get("/reading").json()

        self.assertEqual((reading["reader"], reading["module"]), ("Model", "booking_agent.model.reader"))
        self.assertIs(reading["default"], False)

    def test_asked_for_by_name_the_model_is_looked_for_before_anything_listens(self) -> None:
        from booking_agent.service.__main__ import reader

        clinic = default(starting=date(2026, 9, 7))
        url, _ = serving(self, lambda path, body: (200, {"version": "0.40.0"} if path == "/api/version" else {"models": []}))

        self.assertIsNone(reader(None, url, clinic))
        with self.assertRaises(ModelError) as raised:
            reader("qwen2.5:3b", url, clinic)
        self.assertIn("ollama pull qwen2.5:3b", str(raised.exception))


class TheRecordedRun(unittest.TestCase):
    """The golden calls through the model, replayed from what it answered on the day."""

    def test_there_is_one(self) -> None:
        self.assertTrue(RECORDINGS, "no recorded run in evals/models")

    def test_replayed_every_verdict_is_the_one_recorded(self) -> None:
        from tools.evals import _totals, _verdicts, recorded_run

        for path in RECORDINGS:
            with self.subTest(recording=path.name):
                recorded, results, summary, _ = recorded_run(path)
                self.assertEqual(_verdicts(results), recorded["calls"])
                self.assertEqual(_totals(summary), recorded["totals"])
                self.assertEqual(summary.all.errors, 0)

    def test_replayed_as_json_it_prints_json_and_nothing_else(self) -> None:
        # Found by a review: the line saying the verdicts were the same came
        # after the closing brace, and --json printed something no JSON
        # reader would read.
        import contextlib
        import io

        from tools.evals import main

        printed, said = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(said):
            code = main(["--replay", str(RECORDINGS[0]), "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed.getvalue())["all"]["calls"], 100)
        self.assertIn("the same verdicts as the run that was recorded", said.getvalue())

    def test_every_run_was_made_with_the_prompt_in_this_repository(self) -> None:
        # Both runs, the one that cannot be replayed included: the README
        # compares them, and two runs of different prompts are not comparable.
        written = hashlib.sha256(model.instructions(catalogue()).encode("utf-8")).hexdigest()
        for path in RUNS:
            with self.subTest(run=path.name):
                recorded = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(recorded["instructions_sha256"], written)
                self.assertEqual(recorded["options"], OPTIONS)

    def test_a_request_nobody_recorded_is_an_error_and_not_the_nearest_answer(self) -> None:
        from tools.evals import replaying

        for path in RECORDINGS[:1]:
            recorded = json.loads(path.read_text(encoding="utf-8"))
            send = replaying(recorded)
            with self.assertRaises(ModelError) as raised:
                Model(catalogue(), model=recorded["model"]["model"], send=send).read("a sentence no call says")
            self.assertIn("not in the recording", str(raised.exception))

    def test_a_run_that_cannot_be_replayed_says_so_rather_than_replaying_another(self) -> None:
        from tools.evals import replaying

        for path in RUNS:
            recorded = json.loads(path.read_text(encoding="utf-8"))
            if recorded.get("replayable", True):
                continue
            with self.subTest(run=path.name), self.assertRaises(ModelError) as raised:
                replaying(recorded)
            self.assertIn("cannot be replayed", str(raised.exception))


class TheTwoRuns(unittest.TestCase):
    """What the README says about running the same prompt twice, against the two files."""

    def runs(self) -> tuple[dict, dict]:
        first = HERE / "evals" / "models" / "qwen2.5-3b-first-run.json"
        second = HERE / "evals" / "models" / "qwen2.5-3b.json"
        return json.loads(first.read_text(encoding="utf-8")), json.loads(second.read_text(encoding="utf-8"))

    def test_the_same_verdict_on_every_call(self) -> None:
        # The totals for all the calls and for the held out ones as well. Not
        # the critical ones: the first run is kept as it was counted, before
        # held-piecemeal-01 was marked critical, and the second was recounted.
        first, second = self.runs()
        self.assertEqual(first["calls"], second["calls"])
        for kind in ("all", "held_out", "by_category"):
            self.assertEqual(first["totals"][kind], second["totals"][kind])
        self.assertEqual(second["totals"]["critical"]["calls"], first["totals"]["critical"]["calls"] + 1)

    def test_the_same_first_answer_to_every_request(self) -> None:
        first, second = self.runs()
        self.assertEqual(set(first["answers"]), set(second["answers"]))
        for which, answered in first["answers"].items():
            self.assertEqual(answered["content"], second["answers"][which]["contents"][0])

    def test_and_the_same_requests_answered_differently_later(self) -> None:
        first, second = self.runs()
        self.assertEqual(sorted(first["answered_differently"]), sorted(second["answered_differently"]))
        self.assertEqual(len(second["answered_differently"]), 4)


class RecordingAModelThatChangesItsMind(unittest.TestCase):
    """At temperature zero, with a fixed seed, the first run had four requests answered two ways."""

    def asked_three_times(self):
        from tools.evals import recording

        answers = iter(["A", "B", "A"])
        send, kept = recording(lambda request: {"message": {"content": next(answers)}})
        request = {"model": "m", "messages": [{"role": "user", "content": "yes"}]}
        for _ in range(3):
            send(request)
        return request, kept

    def test_every_answer_is_kept_in_the_order_it_came(self) -> None:
        request, kept = self.asked_three_times()

        self.assertEqual(kept.answers[key(request)]["contents"], ["A", "B", "A"])
        self.assertEqual(kept.changed(), ["yes"])
        self.assertEqual(len(kept.seconds), 3)

    def test_and_replayed_in_that_order_and_not_once_more(self) -> None:
        from tools.evals import replaying

        request, kept = self.asked_three_times()
        send = replaying({"model": {"model": "m"}, "answers": kept.answers})

        self.assertEqual([send(request)["message"]["content"] for _ in range(3)], ["A", "B", "A"])
        with self.assertRaises(ModelError):
            send(request)


if __name__ == "__main__":
    unittest.main()
