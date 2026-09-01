"""Whole conversations, from hello to a reference number.

These are the tests that say whether the agent works, and they are written as
conversations because that is what it is. Nothing is mocked: a real catalogue, a
real diary, the rules reader, and the graph. The clock is passed in, so a hold
running out mid-sentence is three lines rather than a wait.

Each one is a call somebody would actually make, including the ones that go
wrong — which is most of them.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta

from booking_agent.clinic.catalogue import Catalogue, Exam
from booking_agent.clinic.diary import Diary, Room, Session
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Rules
from booking_agent.conversation.state import new

MONDAY = date(2026, 9, 7)
FRIDAY_BEFORE = datetime(2026, 9, 4, 9, 0)


def catalogue() -> Catalogue:
    return Catalogue(
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


def diary() -> Diary:
    rooms = [
        Room(code="MR1", name="MRI room", modalities=frozenset({"MR"})),
        Room(code="XR1", name="X-ray room", modalities=frozenset({"XR"})),
    ]
    days = [MONDAY + timedelta(days=n) for n in range(5)]
    sessions = [
        Session(room=room, day=day, opens=time(9, 0), closes=time(13, 0))
        for room in ("MR1", "XR1")
        for day in days
    ]
    return Diary(rooms, sessions)


def agent(book: Diary | None = None) -> Agent:
    cat = catalogue()
    return Agent(catalogue=cat, diary=book or diary(), reader=Rules(cat), clinic_name="Example Clinic")


class ABookingFromStartToFinish(unittest.TestCase):
    def test_the_whole_call(self) -> None:
        one = agent()
        talk = new("s1")
        now = FRIDAY_BEFORE

        said = one.reply_to(talk, "hello", now=now)
        self.assertIn("book", said.lower())

        said = one.reply_to(talk, "I need an mri of the knee", now=now)
        self.assertIn("left or right", said.lower())

        said = one.reply_to(talk, "left", now=now)
        self.assertIn("contrast", said.lower())

        said = one.reply_to(talk, "without contrast", now=now)
        self.assertIn("name", said.lower())

        said = one.reply_to(talk, "Mario Rossi", now=now)
        self.assertIn("1)", said)

        said = one.reply_to(talk, "the first one", now=now)
        self.assertIn("shall i book", said.lower())

        said = one.reply_to(talk, "yes", now=now)

        self.assertEqual(talk.stage, "booked")
        self.assertIsNotNone(talk.booking)
        assert talk.booking is not None
        self.assertIn(talk.booking.reference, said)
        self.assertEqual(talk.booking.exam_codes, ("MRI-KNEE",))
        self.assertEqual(talk.booking.patient, "Mario Rossi")

    def test_the_slot_really_leaves_the_diary(self) -> None:
        book = diary()
        one = agent(book)
        talk = new("s2")
        now = FRIDAY_BEFORE

        for said in ("mri knee", "right", "with contrast", "Anna Bianchi", "1", "yes"):
            one.reply_to(talk, said, now=now)

        assert talk.booking is not None
        taken = talk.booking.slots[0]
        still_free = [
            slot.starts
            for slot in book.free(modality="MR", minutes=30, day=taken.starts.date(), now=now)
        ]
        self.assertNotIn(taken.starts, still_free)


class WhenTheCallerIsNotUnderstood(unittest.TestCase):
    def test_asked_again_once(self) -> None:
        one = agent()
        talk = new("s3")

        said = one.reply_to(talk, "mmh", now=FRIDAY_BEFORE)
        self.assertIn("did not catch", said.lower())
        self.assertEqual(talk.stage, "gathering")

    def test_and_handed_over_rather_than_asked_a_third_time(self) -> None:
        # An agent that says "sorry, could you repeat that" three times has
        # already lost the caller.
        one = agent()
        talk = new("s4")

        one.reply_to(talk, "mmh", now=FRIDAY_BEFORE)
        one.reply_to(talk, "the thing", now=FRIDAY_BEFORE)
        said = one.reply_to(talk, "you know", now=FRIDAY_BEFORE)

        self.assertEqual(talk.stage, "handed_over")
        self.assertEqual(talk.handed_over, "not_understood")
        self.assertIn("colleague", said.lower())


class WhenTheyWantAPerson(unittest.TestCase):
    def test_they_get_one_immediately(self) -> None:
        one = agent()
        talk = new("s5")

        said = one.reply_to(talk, "can I speak to somebody please", now=FRIDAY_BEFORE)

        self.assertEqual(talk.stage, "handed_over")
        self.assertEqual(talk.handed_over, "asked_for_a_person")
        self.assertIn("colleague", said.lower())

    def test_even_in_the_middle_of_a_booking(self) -> None:
        one = agent()
        talk = new("s6")

        one.reply_to(talk, "mri knee", now=FRIDAY_BEFORE)
        one.reply_to(talk, "left", now=FRIDAY_BEFORE)
        one.reply_to(talk, "put me through to a human", now=FRIDAY_BEFORE)

        self.assertEqual(talk.handed_over, "asked_for_a_person")
        # And what they had said so far goes with them.
        self.assertIn("MRI knee", talk.handover_note)


class WhatCannotBeBookedHere(unittest.TestCase):
    def test_is_explained_and_handed_on(self) -> None:
        one = agent()
        talk = new("s7")

        said = one.reply_to(talk, "I need a ct angiography", now=FRIDAY_BEFORE)

        self.assertEqual(talk.handed_over, "cannot_be_booked_here")
        self.assertIn("doctor", said)
        self.assertIn("colleague", said.lower())


class WhenTheSlotGoes(unittest.TestCase):
    def test_taken_between_offering_and_answering(self) -> None:
        book = diary()
        one = agent(book)
        talk = new("s8")
        now = FRIDAY_BEFORE

        for said in ("mri knee", "left", "no contrast", "Mario Rossi"):
            one.reply_to(talk, said, now=now)

        # Somebody else takes the first offered slot in the meantime.
        book.hold([talk.offered[0]], now=now)

        said = one.reply_to(talk, "the first one", now=now)
        self.assertIn("just gone", said.lower())

    def test_the_hold_runs_out_while_they_decide(self) -> None:
        one = agent()
        talk = new("s9")
        now = FRIDAY_BEFORE

        for said in ("mri knee", "left", "no contrast", "Mario Rossi", "1"):
            one.reply_to(talk, said, now=now)

        much_later = now + timedelta(hours=1)
        said = one.reply_to(talk, "yes", now=much_later)

        self.assertIn("went while we were talking", said.lower())
        self.assertIsNone(talk.booking)


class OtherThingsPeopleSay(unittest.TestCase):
    def test_asking_when_the_clinic_is_open(self) -> None:
        one = agent()
        talk = new("s10")

        said = one.reply_to(talk, "what time do you open", now=FRIDAY_BEFORE)
        self.assertIn("open", said.lower())
        self.assertNotEqual(talk.stage, "handed_over")

    def test_changing_an_existing_booking_goes_to_a_person(self) -> None:
        # It needs to know it is really them, and this agent cannot check.
        one = agent()
        talk = new("s11")

        one.reply_to(talk, "I need to cancel my appointment", now=FRIDAY_BEFORE)
        self.assertEqual(talk.stage, "handed_over")

    def test_not_liking_the_times_offered(self) -> None:
        # The times have to be *different* times. This test used to assert the
        # opposite — that nothing had changed, because nothing had been held —
        # and it passed while the agent answered "no, something else" by
        # reading the same three times back. It took a transcript to see it:
        # the assertion was true, and it was about the wrong thing.
        one = agent()
        talk = new("s12")
        now = FRIDAY_BEFORE

        for said in ("mri knee", "left", "no contrast", "Mario Rossi"):
            one.reply_to(talk, said, now=now)

        first = [(slot.room, slot.starts) for slot in talk.offered]
        said = one.reply_to(talk, "no, something else", now=now)
        second = [(slot.room, slot.starts) for slot in talk.offered]

        self.assertIn("1)", said)
        self.assertTrue(second)
        self.assertEqual(set(first) & set(second), set())

    def test_asking_for_a_slot_that_was_not_offered(self) -> None:
        one = agent()
        talk = new("s13")
        now = FRIDAY_BEFORE

        for said in ("mri knee", "left", "no contrast", "Mario Rossi"):
            one.reply_to(talk, said, now=now)

        said = one.reply_to(talk, "number 9", now=now)
        self.assertIn("only have", said.lower())


class WhenNothingIsFree(unittest.TestCase):
    def test_a_person_is_told_rather_than_the_caller_kept_waiting(self) -> None:
        # An empty diary. A person can offer a waiting list; this cannot.
        empty = Diary(
            [Room(code="MR1", name="MRI room", modalities=frozenset({"MR"}))],
            [],
        )
        one = agent(empty)
        talk = new("s14")

        for said in ("mri knee", "left", "no contrast"):
            one.reply_to(talk, said, now=FRIDAY_BEFORE)
        said = one.reply_to(talk, "Mario Rossi", now=FRIDAY_BEFORE)

        self.assertEqual(talk.handed_over, "booking_failed")
        self.assertIn("two weeks", said)


class TheAmbiguousCase(unittest.TestCase):
    def test_a_word_that_names_two_exams_is_asked_about(self) -> None:
        both = Catalogue(
            [
                Exam(code="MRI-KNEE", name="MRI knee", modality="MR", minutes=30, price=180.0),
                Exam(code="XR-KNEE", name="X-ray knee", modality="XR", minutes=10, price=45.0),
            ]
        )
        one = Agent(catalogue=both, diary=diary(), reader=Rules(both))
        talk = new("s15")

        said = one.reply_to(talk, "knee", now=FRIDAY_BEFORE)

        self.assertIn("did you mean", said.lower())
        self.assertIn("MRI knee", said)
        self.assertIn("X-ray knee", said)


class WhatTheTranscriptsCaught(unittest.TestCase):
    """Four defects that seventy passing tests did not see.

    Every one of them was found the first time whole conversations were run
    through the agent instead of the turns it had been written against. They
    are kept here as tests so they stay fixed, and kept together so it is
    obvious what kind of mistake they all were: each one is a case the code
    handled correctly for the example it was written for.
    """

    def test_a_message_of_nothing_but_common_words_books_nothing(self) -> None:
        # "it's about the thing" used to reach "and what name should I put it
        # under?", because a synonym reading "scan of the tummy" had put the
        # word *the* into an exam's vocabulary.
        both = Catalogue(
            [
                Exam(
                    code="US-ABDOMEN",
                    name="Ultrasound abdomen",
                    modality="US",
                    minutes=20,
                    price=90.0,
                    synonyms=("scan of the tummy",),
                ),
            ]
        )
        one = Agent(catalogue=both, diary=diary(), reader=Rules(both))
        talk = new("t1")

        said = one.reply_to(talk, "it's about the thing", now=FRIDAY_BEFORE)

        self.assertIn("did not catch", said.lower())
        self.assertEqual(talk.requests, [])

    def test_a_whole_sentence_is_not_ambiguous_just_because_it_is_long(self) -> None:
        # Said in one breath, the way people who know what they want say it.
        one = agent()
        talk = new("t2")

        said = one.reply_to(
            talk, "I need an MRI of the left knee without contrast", now=FRIDAY_BEFORE
        )

        self.assertNotIn("did you mean", said.lower())
        self.assertEqual(talk.requests[0].exam.code, "MRI-KNEE")
        self.assertEqual(talk.requests[0].side, "left")
        self.assertIs(talk.requests[0].contrast, False)

    def test_the_answer_to_did_you_mean_is_read_against_the_question(self) -> None:
        both = Catalogue(
            [
                Exam(code="MRI-KNEE", name="MRI knee", modality="MR", minutes=30, price=180.0),
                Exam(code="XR-KNEE", name="X-ray knee", modality="XR", minutes=10, price=45.0),
                # A second MRI, so "the mri one" names two exams in the
                # catalogue and exactly one of the two that were asked about.
                Exam(code="MRI-SPINE", name="MRI whole spine", modality="MR", minutes=75, price=420.0),
            ]
        )
        one = Agent(catalogue=both, diary=diary(), reader=Rules(both))
        talk = new("t3")

        one.reply_to(talk, "knee", now=FRIDAY_BEFORE)
        one.reply_to(talk, "the mri one", now=FRIDAY_BEFORE)

        self.assertEqual([request.exam.code for request in talk.requests], ["MRI-KNEE"])

    def test_the_answer_can_also_be_by_position(self) -> None:
        both = Catalogue(
            [
                Exam(code="MRI-KNEE", name="MRI knee", modality="MR", minutes=30, price=180.0),
                Exam(code="XR-KNEE", name="X-ray knee", modality="XR", minutes=10, price=45.0),
            ]
        )
        one = Agent(catalogue=both, diary=diary(), reader=Rules(both))
        talk = new("t4")

        one.reply_to(talk, "knee", now=FRIDAY_BEFORE)
        one.reply_to(talk, "the second one", now=FRIDAY_BEFORE)

        self.assertEqual([request.exam.code for request in talk.requests], ["XR-KNEE"])

    def test_and_they_may_change_their_mind_instead_of_answering(self) -> None:
        one = agent()
        talk = new("t5")

        one.reply_to(talk, "knee", now=FRIDAY_BEFORE)
        one.reply_to(talk, "actually a chest x-ray", now=FRIDAY_BEFORE)

        self.assertEqual([request.exam.code for request in talk.requests], ["XR-CHEST"])
        self.assertEqual(talk.candidates, [])

    def test_an_answer_may_say_more_than_it_was_asked(self) -> None:
        # Found while writing the README, which is a worse place to find it
        # than a test and a better one than a demonstration. Asked "left or
        # right?", this caller says "left, no contrast": the answer, and then
        # the next one. The side was taken and the rest thrown away, so the
        # agent asked about contrast and the caller repeated themselves.
        one = agent()
        talk = new("t7")

        one.reply_to(talk, "mri knee", now=FRIDAY_BEFORE)
        said = one.reply_to(talk, "left, no contrast", now=FRIDAY_BEFORE)

        self.assertEqual(talk.requests[0].side, "left")
        self.assertIs(talk.requests[0].contrast, False)
        self.assertIn("name", said.lower())

    def test_in_either_order(self) -> None:
        one = agent()
        talk = new("t8")

        one.reply_to(talk, "mri knee", now=FRIDAY_BEFORE)
        one.reply_to(talk, "no contrast, left", now=FRIDAY_BEFORE)

        self.assertEqual(talk.requests[0].side, "left")
        self.assertIs(talk.requests[0].contrast, False)

    def test_but_yes_still_only_answers_the_question_that_was_asked(self) -> None:
        # "Yes" means with contrast when contrast is what was asked about, and
        # nothing at all when the question was left or right.
        one = agent()
        talk = new("t9")

        one.reply_to(talk, "mri knee", now=FRIDAY_BEFORE)
        said = one.reply_to(talk, "yes", now=FRIDAY_BEFORE)

        self.assertIsNone(talk.requests[0].side)
        self.assertIsNone(talk.requests[0].contrast)
        self.assertIn("left or right", said.lower())

    def test_an_appointment_they_already_have_is_its_own_reason(self) -> None:
        # It used to be counted as "not understood", which would put a working
        # part of the agent on somebody's list of things to fix.
        one = agent()
        talk = new("t6")

        said = one.reply_to(talk, "I need to move my appointment on Thursday", now=FRIDAY_BEFORE)

        self.assertEqual(talk.handed_over, "already_booked")
        self.assertIn("really you", said)


if __name__ == "__main__":
    unittest.main()
