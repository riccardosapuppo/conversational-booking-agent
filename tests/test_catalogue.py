"""Turning what somebody said into one row of a catalogue.

This is where a booking agent is right or wrong before it does anything else,
and the tests here are the sentences people actually say rather than the ones
the catalogue was written for.
"""

from __future__ import annotations

import unittest

from booking_agent.clinic.catalogue import Catalogue, Exam, contrast_in, load, side_in


def catalogue() -> Catalogue:
    return Catalogue(
        [
            Exam(
                code="MRI-KNEE",
                name="MRI knee",
                modality="MR",
                minutes=30,
                price=180.0,
                synonyms=("magnetic resonance knee", "resonance knee"),
                needs_side=True,
                needs_contrast=True,
            ),
            Exam(
                code="XR-KNEE",
                name="X-ray knee",
                modality="XR",
                minutes=10,
                price=45.0,
                synonyms=("radiography knee", "knee radiograph"),
                needs_side=True,
            ),
            Exam(
                code="XR-CHEST",
                name="X-ray chest",
                modality="XR",
                minutes=10,
                price=40.0,
                synonyms=("chest radiograph",),
            ),
            Exam(
                code="CT-ANGIO",
                name="CT angiography",
                modality="CT",
                minutes=25,
                price=320.0,
                bookable=False,
                unbookable_reason="a doctor has to approve the contrast dose first",
            ),
        ]
    )


class Searching(unittest.TestCase):
    def test_a_word_finds_everything_it_could_mean(self) -> None:
        # "knee" is both knee exams. Picking one because it happened to be
        # first is how an agent books an x-ray for somebody who wanted an MRI.
        found = {match.exam.code for match in catalogue().search("knee")}
        self.assertEqual(found, {"MRI-KNEE", "XR-KNEE"})

    def test_naming_the_modality_settles_it(self) -> None:
        best = catalogue().search("mri knee")[0]
        self.assertEqual(best.exam.code, "MRI-KNEE")

    def test_the_words_people_use_are_in_the_catalogue(self) -> None:
        # Nobody says "MRI knee". They say resonance, or radiography.
        found = catalogue().search("resonance knee")
        self.assertEqual(found[0].exam.code, "MRI-KNEE")

        found = catalogue().search("knee radiograph")
        self.assertEqual(found[0].exam.code, "XR-KNEE")

    def test_nothing_asked_is_nothing_found(self) -> None:
        self.assertEqual(catalogue().search(""), [])
        self.assertEqual(catalogue().search("   ...   "), [])

    def test_something_nobody_offers(self) -> None:
        self.assertEqual(catalogue().search("haircut"), [])

    def test_the_same_question_gets_the_same_answer(self) -> None:
        # Ties keep catalogue order rather than whatever the sort felt like, so
        # an agent does not say two different things to two callers.
        first = [match.exam.code for match in catalogue().search("knee")]
        second = [match.exam.code for match in catalogue().search("knee")]
        self.assertEqual(first, second)


class Resolving(unittest.TestCase):
    def test_one_exam_named_completely(self) -> None:
        request = catalogue().resolve("mri of the left knee with contrast")

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.exam.code, "MRI-KNEE")
        self.assertEqual(request.side, "left")
        self.assertIs(request.contrast, True)
        self.assertTrue(request.complete)

    def test_two_equally_good_answers_is_not_an_answer(self) -> None:
        # "knee" is both knee exams, equally. Choosing is the caller's, and an
        # agent that chooses here is an agent that is wrong half the time.
        self.assertIsNone(catalogue().resolve("knee"))

    def test_what_is_still_missing_is_named(self) -> None:
        request = catalogue().resolve("mri knee")
        assert request is not None

        self.assertEqual(request.missing(), ("side", "contrast"))
        self.assertFalse(request.complete)

    def test_an_exam_with_no_sides_is_never_asked_which(self) -> None:
        # Asking which side for a chest x-ray is how an agent sounds like a
        # form somebody has to fill in.
        request = catalogue().resolve("chest radiograph")
        assert request is not None
        self.assertEqual(request.missing(), ())


class ReadingTheParts(unittest.TestCase):
    def test_a_side_is_found_however_it_is_said(self) -> None:
        self.assertEqual(side_in("left knee"), "left")
        self.assertEqual(side_in("the right one"), "right")
        self.assertEqual(side_in("both knees"), "both")
        self.assertEqual(side_in("bilateral"), "both")

    def test_no_side_mentioned_is_not_a_side(self) -> None:
        self.assertIsNone(side_in("mri knee"))

    def test_contrast_has_three_answers_and_not_two(self) -> None:
        # Not mentioning contrast is not the same as refusing it, and reading
        # silence as a no is how somebody arrives for the wrong scan.
        self.assertIs(contrast_in("with contrast"), True)
        self.assertIs(contrast_in("no-contrast please"), False)
        self.assertIsNone(contrast_in("mri knee"))

    def test_the_words_survive_punctuation_and_case(self) -> None:
        self.assertEqual(side_in("LEFT!"), "left")
        self.assertEqual(side_in("...right..."), "right")


class Loading(unittest.TestCase):
    def test_a_catalogue_is_built_from_plain_data(self) -> None:
        built = load(
            [
                {
                    "code": "US-ABD",
                    "name": "Ultrasound abdomen",
                    "modality": "US",
                    "minutes": 20,
                    "price": 90,
                    "synonyms": ["echography abdomen"],
                }
            ]
        )

        self.assertEqual(len(built), 1)
        found = built.search("echography abdomen")
        self.assertEqual(found[0].exam.code, "US-ABD")

    def test_two_exams_cannot_share_a_code(self) -> None:
        # Whichever is found second would be unreachable for ever, and nothing
        # would say so.
        with self.assertRaises(ValueError):
            Catalogue(
                [
                    Exam(code="X", name="One", modality="XR", minutes=5, price=1.0),
                    Exam(code="X", name="Two", modality="XR", minutes=5, price=1.0),
                ]
            )


class WhatCannotBeBooked(unittest.TestCase):
    def test_it_is_found_and_explained_rather_than_hidden(self) -> None:
        # Hiding it makes the clinic look like it does not do the thing. The
        # caller asked for it because somebody told them to.
        found = catalogue().search("ct angiography")
        self.assertEqual(found[0].exam.code, "CT-ANGIO")
        self.assertFalse(found[0].exam.bookable)
        self.assertIn("doctor", found[0].exam.unbookable_reason)


if __name__ == "__main__":
    unittest.main()
