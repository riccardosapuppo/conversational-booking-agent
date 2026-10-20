"""The golden calls, and the scorer that reads them.

Two kinds of check. The scorer's own, first, because a scorer that cannot tell
the wrong knee from the right one passes everything it is shown, and nothing
downstream of it would notice. Then the golden set as a whole, against the two
thresholds written next to the cases, which is what this file is for: a change
that makes the agent worse fails here, before it fails a caller.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from booking_agent.clinic.build import from_file
from booking_agent.clinic.catalogue import Request
from booking_agent.clinic.diary import Slot
from booking_agent.conversation.reading import Reading
from booking_agent.conversation.state import new
from tools.evals import (
    CATEGORIES,
    GOLDEN,
    BadCase,
    Result,
    judge,
    load,
    run,
    run_all,
    seed,
    summarise,
)
from tools.transcripts import lines_of

HERE = Path(__file__).resolve().parents[1]


def golden_with(case: dict, **top) -> Path:
    """A golden file of one case, written somewhere temporary, for the loader to refuse."""
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    raw["cases"] = [case]
    raw.update(top)
    folder = Path(tempfile.mkdtemp())
    (folder / "evals").mkdir()
    (folder / "data").mkdir()
    (folder / "data" / "clinic.json").write_text((HERE / "data" / "clinic.json").read_text(encoding="utf-8"), encoding="utf-8")
    path = folder / "evals" / "golden.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def a_case(**changed) -> dict:
    case = {
        "id": "made-up",
        "category": "complete",
        "critical": True,
        "held_out": False,
        "why": "here to be broken",
        "said": ["I need an MRI of the left knee without contrast", "Mario Rossi", "1", "yes"],
        "expect": {
            "tool": "confirm_booking",
            "exam": "MRI-KNEE",
            "side": "left",
            "contrast": False,
            "patient": "Mario Rossi",
            "starts": "2026-09-07T09:00",
            "room": "MR1",
        },
    }
    case.update(changed)
    return case


class TheFile(unittest.TestCase):
    def test_it_loads_and_is_big_enough_to_mean_something(self) -> None:
        golden = load()
        self.assertGreaterEqual(len(golden.cases), 50)
        self.assertLessEqual(len(golden.cases), 100)

    def test_every_kind_of_call_is_in_it(self) -> None:
        golden = load()
        for category in CATEGORIES:
            with self.subTest(category=category):
                self.assertGreaterEqual(
                    sum(case.category == category for case in golden.cases), 5, f"too few {category} calls"
                )

    def test_and_a_good_part_of_it_is_critical(self) -> None:
        golden = load()
        self.assertGreaterEqual(sum(case.critical for case in golden.cases), 20)

    def test_the_ten_transcripts_are_in_it_with_what_should_have_happened(self) -> None:
        # The transcripts say how a call ended; the golden set says how it
        # should have. The ten that came first are the same calls in both.
        golden = load()
        lines = {case.said for case in golden.cases}
        transcripts = sorted((HERE / "data" / "conversations").glob("*.txt"))

        self.assertEqual(len(transcripts), 10, "the transcripts this check is about have moved")
        for path in transcripts:
            with self.subTest(transcript=path.name):
                self.assertTrue(tuple(lines_of(path)) in lines, f"{path.name} is not one of the golden calls")


class WhatTheLoaderRefuses(unittest.TestCase):
    """A case that cannot be scored as written is refused before anything runs.

    Otherwise a typo is a case that can never pass, sitting in the totals as a
    failure of the agent, or a case that can never fail, sitting there as a
    success of it.
    """

    def refused(self, path: Path) -> None:
        with self.assertRaises(BadCase):
            load(path)

    def test_the_case_as_written_is_accepted(self) -> None:
        # Otherwise every refusal below could be the loader refusing anything.
        self.assertEqual(len(load(golden_with(a_case())).cases), 1)

    def test_a_key_nobody_reads(self) -> None:
        self.refused(golden_with(a_case(expect={"tool": "hand_over", "reasn": "not_understood"})))

    def test_a_reason_the_agent_does_not_have(self) -> None:
        self.refused(golden_with(a_case(expect={"tool": "hand_over", "reason": "gave_up"})))

    def test_an_exam_the_clinic_does_not_have(self) -> None:
        self.refused(golden_with(a_case(expect={**a_case()["expect"], "exam": "MRI-ELBOW"})))

    def test_a_side_for_an_exam_that_has_none(self) -> None:
        wanted = {**a_case()["expect"], "exam": "XR-CHEST", "contrast": None, "room": "XR1"}
        self.refused(golden_with(a_case(expect=wanted)))

    def test_no_side_for_an_exam_that_needs_one(self) -> None:
        self.refused(golden_with(a_case(expect={**a_case()["expect"], "side": None})))

    def test_two_ways_of_saying_when(self) -> None:
        wanted = {**a_case()["expect"], "between": ["2026-09-07T09:00", "2026-09-07T10:00"]}
        self.refused(golden_with(a_case(expect=wanted)))

    def test_an_empty_line_said(self) -> None:
        self.refused(golden_with(a_case(said=["mri knee", " "])))

    def test_a_kind_of_call_nobody_reports(self) -> None:
        self.refused(golden_with(a_case(category="misc")))

    def test_a_threshold_that_is_not_a_share(self) -> None:
        self.refused(golden_with(a_case(), thresholds={"critical": 1.5, "overall": 0.5}))

    def test_no_cases_at_all(self) -> None:
        path = golden_with(a_case())
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["cases"] = []
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.refused(path)


class TheScorer(unittest.TestCase):
    """Verdicts on calls whose ending is known, built by hand."""

    def setUp(self) -> None:
        self.golden = load()
        self.case = load(golden_with(a_case())).cases[0]
        self.now = self.golden.now

    def clinic(self):
        return from_file(self.golden.clinic, starting=self.golden.laid_out_from)

    def booked(self, clinic, *, side: str, contrast: bool, patient: str = "Mario Rossi"):
        """A booking put into the diary directly, as if a conversation had made it."""
        exam = clinic.catalogue.get("MRI-KNEE")
        held = clinic.diary.hold([Slot(room="MR1", starts=datetime(2026, 9, 7, 9, 0), minutes=exam.minutes)], now=self.now)
        return clinic.diary.confirm(
            held.reference, requests=[Request(exam=exam, side=side, contrast=contrast)], patient=patient, now=self.now
        )

    def test_the_right_booking_passes(self) -> None:
        self.assertEqual(run(self.case, self.golden).verdict, "pass")

    def test_the_wrong_knee_is_harm_and_not_a_miss(self) -> None:
        clinic = self.clinic()
        self.booked(clinic, side="right", contrast=False)

        judged = judge(self.case, new("x"), clinic, [], [], now=self.now)

        self.assertEqual(judged.verdict, "harm")
        self.assertIn("the side is right, not left", judged.why[0])

    def test_and_so_is_the_wrong_contrast_and_the_wrong_patient(self) -> None:
        for side, contrast, patient in (("left", True, "Mario Rossi"), ("left", False, "yes")):
            with self.subTest(contrast=contrast, patient=patient):
                clinic = self.clinic()
                self.booked(clinic, side=side, contrast=contrast, patient=patient)
                self.assertEqual(judge(self.case, new("x"), clinic, [], [], now=self.now).verdict, "harm")

    def test_a_booking_when_none_was_wanted_is_harm(self) -> None:
        handover = load(golden_with(a_case(expect={"tool": "hand_over", "reason": "asked_for_a_person"}))).cases[0]
        clinic = self.clinic()
        self.booked(clinic, side="left", contrast=False)

        self.assertEqual(judge(handover, new("x"), clinic, [], [], now=self.now).verdict, "harm")

    def test_a_person_instead_of_a_booking_is_a_miss(self) -> None:
        talk = new("x")
        talk.hand_over("asked_for_a_person")

        judged = judge(self.case, talk, self.clinic(), [], [], now=self.now)

        self.assertEqual(judged.verdict, "miss")
        self.assertEqual(judged.outcome, "hand_over")

    def test_a_person_for_the_wrong_reason_is_a_miss(self) -> None:
        handover = load(golden_with(a_case(expect={"tool": "hand_over", "reason": "asked_for_a_person"}))).cases[0]
        talk = new("x")
        talk.hand_over("not_understood")

        self.assertEqual(judge(handover, talk, self.clinic(), [], [], now=self.now).verdict, "miss")

    def test_a_slot_still_held_when_the_call_is_over_is_a_miss(self) -> None:
        handover = load(golden_with(a_case(expect={"tool": "hand_over", "reason": "asked_for_a_person"}))).cases[0]
        clinic = self.clinic()
        clinic.diary.hold([Slot(room="MR1", starts=datetime(2026, 9, 7, 9, 0), minutes=30)], now=self.now)
        talk = new("x")
        talk.hand_over("asked_for_a_person")

        judged = judge(handover, talk, clinic, [], [], now=self.now)

        self.assertEqual(judged.verdict, "miss")
        self.assertIn("held for nobody", judged.why[0])

    def test_a_booking_that_was_already_there_and_is_touched_is_harm(self) -> None:
        handover = load(golden_with(a_case(expect={"tool": "hand_over", "reason": "already_booked"}))).cases[0]
        clinic = self.clinic()
        already = seed(clinic, load(golden_with(a_case(given={"booked": [{
            "exam": "MRI-KNEE", "side": "right", "contrast": False, "patient": "Anna Bianchi",
            "starts": "2026-09-10T09:00", "room": "MR1",
        }]}))).cases[0].given[0], self.now)
        talk = new("x")
        talk.hand_over("already_booked")

        self.assertEqual(judge(handover, talk, clinic, [already], [], now=self.now).verdict, "pass")

        clinic.diary.cancel(already.reference)
        self.assertEqual(judge(handover, talk, clinic, [already], [], now=self.now).verdict, "harm")

    def test_a_phrase_the_case_asks_for_is_looked_for_in_what_was_said(self) -> None:
        answering = load(golden_with(a_case(expect={"tool": "clinic_information", "says": ["Mulberry Lane"]}))).cases[0]
        talk = new("x")
        talk.moves.append("answer")

        heard = [("where are you", "Kesterby is at 14 Mulberry Lane.")]
        self.assertEqual(judge(answering, talk, self.clinic(), [], heard, now=self.now).verdict, "pass")

        silent = [("where are you", "Would you like to book something?")]
        self.assertEqual(judge(answering, talk, self.clinic(), [], silent, now=self.now).verdict, "miss")


class TheTotals(unittest.TestCase):
    def results(self, *verdicts: tuple[str, bool]) -> list[Result]:
        golden = load(golden_with(a_case()))
        case = golden.cases[0]
        return [
            Result(case=type(case)(**{**case.__dict__, "id": f"c{n}", "critical": critical}), verdict=verdict)
            for n, (verdict, critical) in enumerate(verdicts)
        ]

    def test_harm_outside_a_critical_case_fails_the_run_however_good_the_totals(self) -> None:
        golden = load(golden_with(a_case(), thresholds={"critical": 0.0, "overall": 0.0}))
        summary = summarise(self.results(("pass", True), ("harm", False)), golden)

        self.assertEqual(summary.mislabelled, ("c1",))
        self.assertFalse(summary.met)

    def test_an_error_is_not_a_verdict_and_fails_the_run(self) -> None:
        golden = load(golden_with(a_case(), thresholds={"critical": 0.0, "overall": 0.0}))

        class Raises:
            def __init__(self, catalogue) -> None:
                pass

            def read(self, text: str, *, expecting: str = "") -> Reading:
                raise RuntimeError("the reader fell over")

        results = [run(golden.cases[0], golden, reader=Raises)]

        self.assertEqual(results[0].verdict, "error")
        self.assertFalse(summarise(results, golden).met)

    def test_a_run_of_nothing_meets_nothing(self) -> None:
        golden = load(golden_with(a_case(), thresholds={"critical": 0.0, "overall": 0.0}))
        self.assertFalse(summarise([], golden).met)

    def test_below_either_threshold_is_below(self) -> None:
        golden = load(golden_with(a_case(), thresholds={"critical": 0.5, "overall": 0.5}))
        self.assertTrue(summarise(self.results(("pass", True), ("miss", False)), golden).met)
        self.assertFalse(summarise(self.results(("miss", True), ("pass", False)), golden).met)
        self.assertFalse(summarise(self.results(("pass", True), ("miss", False), ("miss", False)), golden).met)


class TheGoldenSet(unittest.TestCase):
    """The check this file exists for: the agent, against its own golden calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = load()
        cls.results = run_all(cls.golden)
        cls.summary = summarise(cls.results, cls.golden)

    def failing(self) -> str:
        return "\n".join(
            f"{result.verdict:>5}  {result.case.id}: {'; '.join(result.why)}"
            for result in self.results
            if result.verdict != "pass"
        )

    def test_nothing_raised(self) -> None:
        self.assertEqual(self.summary.all.errors, 0, self.failing())

    def test_no_harm_where_the_case_is_not_marked_critical(self) -> None:
        self.assertEqual(self.summary.mislabelled, (), self.failing())

    def test_it_meets_both_thresholds(self) -> None:
        self.assertGreaterEqual(self.summary.critical.share, self.golden.critical_threshold, self.failing())
        self.assertGreaterEqual(self.summary.all.share, self.golden.overall_threshold, self.failing())

    def test_and_the_thresholds_are_what_was_measured_rather_than_a_comfortable_margin(self) -> None:
        # The thresholds go up when the agent does, and are written as the
        # share measured, rounded down to two places. With fewer than a hundred
        # calls one call is more than a hundredth, so one call that stops
        # passing is below the line. A threshold set comfortably low would let
        # several go before anybody heard about it.
        for name, measured, written in (
            ("critical", self.summary.critical.share, self.golden.critical_threshold),
            ("overall", self.summary.all.share, self.golden.overall_threshold),
        ):
            with self.subTest(threshold=name):
                self.assertEqual(
                    written,
                    math.floor(measured * 100) / 100,
                    f"{name}: measured {measured:.4f}, so the threshold is {math.floor(measured * 100) / 100:.2f}",
                )


if __name__ == "__main__":
    unittest.main()
