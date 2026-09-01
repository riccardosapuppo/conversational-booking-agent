"""What people actually say, and what the agent makes of it.

Every message in this file is one somebody would say out loud. The point of the
rules reader is not that it is clever — it is that the agent's behaviour can be
tested without a model, so the interesting situations are written down here
rather than hoped for.

The most important test in the file is the last one: not understanding has to
stay an answer. A reader that guesses produces a confident booking for the
wrong thing.
"""

from __future__ import annotations

import unittest

from booking_agent.clinic.catalogue import Catalogue, Exam
from booking_agent.conversation.reading import Rules


def reader() -> Rules:
    return Rules(
        Catalogue(
            [
                Exam(
                    code="MRI-KNEE",
                    name="MRI knee",
                    modality="MR",
                    minutes=30,
                    price=180.0,
                    synonyms=("resonance knee",),
                    needs_side=True,
                    needs_contrast=True,
                ),
                Exam(
                    code="XR-CHEST",
                    name="X-ray chest",
                    modality="XR",
                    minutes=10,
                    price=40.0,
                    synonyms=("chest radiograph",),
                ),
            ]
        )
    )


class AskingForAPerson(unittest.TestCase):
    def test_however_it_is_asked(self) -> None:
        for said in (
            "can I speak to a person",
            "put me through to an operator",
            "I want to talk to a human",
            "is there somebody there",
        ):
            with self.subTest(said=said):
                self.assertEqual(reader().read(said).intent, "operator")

    def test_a_refusal_that_asks_for_a_person_is_asking_for_a_person(self) -> None:
        # "No, give me a human" is not a caller turning down a slot. Reading it
        # as one traps exactly the person who most wants out.
        self.assertEqual(reader().read("no, I want a human").intent, "operator")


class NamingAnExam(unittest.TestCase):
    def test_the_catalogue_decides_what_is_an_exam(self) -> None:
        # Nobody wrote the word "knee" into the reader. It knows because the
        # catalogue does, so a clinic that adds an exam does not edit this file.
        reading = reader().read("I need a resonance of the knee")
        self.assertEqual(reading.intent, "book")
        self.assertTrue(reading.exam_text)

    def test_the_side_and_the_contrast_come_with_it(self) -> None:
        reading = reader().read("mri of the left knee with contrast")

        self.assertEqual(reading.side, "left")
        self.assertIs(reading.contrast, True)

    def test_a_refusal_of_contrast_survives(self) -> None:
        reading = reader().read("mri knee, no contrast")
        self.assertIs(reading.contrast, False)

    def test_wanting_an_appointment_without_saying_what(self) -> None:
        reading = reader().read("I'd like to book an appointment")

        self.assertEqual(reading.intent, "book")
        self.assertFalse(reading.exam_text)


class ChoosingATime(unittest.TestCase):
    def test_only_when_times_were_offered(self) -> None:
        # "two" in "two exams" is not a choice of slot.
        self.assertIsNone(reader().read("two").which)
        self.assertEqual(reader().read("the second one", expecting="slot").which, 2)

    def test_said_as_a_word_or_a_number(self) -> None:
        for said, expected in (("the first", 1), ("2", 2), ("third please", 3), ("number 4", 4)):
            with self.subTest(said=said):
                self.assertEqual(reader().read(said, expecting="slot").which, expected)

    def test_agreeing_and_refusing(self) -> None:
        self.assertEqual(reader().read("yes please").intent, "agree")
        self.assertEqual(reader().read("perfect").intent, "agree")
        self.assertEqual(reader().read("no thanks").intent, "refuse")
        self.assertEqual(reader().read("something else").intent, "refuse")


class GivingAName(unittest.TestCase):
    def test_only_when_a_name_was_asked_for(self) -> None:
        # A word in the middle of a booking is not necessarily the patient.
        self.assertEqual(reader().read("Mario Rossi").intent, "unclear")
        self.assertEqual(reader().read("Mario Rossi", expecting="patient").name, "Mario Rossi")

    def test_a_sentence_is_not_a_name(self) -> None:
        reading = reader().read(
            "well it is for my mother actually", expecting="patient"
        )
        self.assertEqual(reading.name, "")


class OtherThings(unittest.TestCase):
    def test_questions_about_the_clinic(self) -> None:
        for said in ("what time do you open", "where are you", "is there parking"):
            with self.subTest(said=said):
                self.assertEqual(reader().read(said).intent, "info")

    def test_changing_something_already_booked(self) -> None:
        for said in ("I need to cancel", "can I move my appointment"):
            with self.subTest(said=said):
                self.assertEqual(reader().read(said).intent, "manage")

    def test_saying_hello(self) -> None:
        self.assertEqual(reader().read("good morning").intent, "greeting")


class NotUnderstanding(unittest.TestCase):
    def test_is_an_answer_and_not_a_guess(self) -> None:
        # The most important behaviour in the file. A reader that reaches for
        # the nearest intent books the wrong thing confidently, and the caller
        # finds out on the day.
        for said in ("mmh", "it's about the thing from last week", "asdfgh"):
            with self.subTest(said=said):
                reading = reader().read(said)
                self.assertEqual(reading.intent, "unclear")
                self.assertFalse(reading)

    def test_nothing_said_is_not_understood_either(self) -> None:
        self.assertEqual(reader().read("").intent, "unclear")
        self.assertEqual(reader().read("   ").intent, "unclear")

    def test_a_reading_is_falsy_only_when_it_failed(self) -> None:
        self.assertTrue(reader().read("mri knee"))
        self.assertFalse(reader().read("asdfgh"))


if __name__ == "__main__":
    unittest.main()
