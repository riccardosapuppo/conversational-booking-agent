"""Offering a slot, keeping it while somebody decides, and losing it.

Every test here is a moment that happens on a real telephone: two callers on
the same slot, somebody who takes twenty minutes to say yes, a room that closes
before the exam would finish. The clock is passed in rather than read, so these
can be written down instead of waited for.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta

from booking_agent.clinic.diary import (
    Diary,
    HoldExpired,
    Room,
    Session,
    Slot,
    SlotGone,
    working_week,
)

MONDAY = date(2026, 9, 7)
NINE = datetime(2026, 9, 7, 9, 0)
EIGHT_THE_DAY_BEFORE = datetime(2026, 9, 6, 8, 0)


def diary() -> Diary:
    rooms = [
        Room(code="MR1", name="MRI room", modalities=frozenset({"MR"})),
        Room(code="XR1", name="X-ray room", modalities=frozenset({"XR"})),
        Room(code="XR2", name="X-ray room 2", modalities=frozenset({"XR"})),
    ]
    sessions = [
        Session(room="MR1", day=MONDAY, opens=time(9, 0), closes=time(12, 0)),
        Session(room="XR1", day=MONDAY, opens=time(9, 0), closes=time(10, 0)),
        Session(room="XR2", day=MONDAY, opens=time(9, 0), closes=time(10, 0)),
    ]
    return Diary(rooms, sessions)


class Looking(unittest.TestCase):
    def test_a_room_is_only_offered_for_what_it_can_do(self) -> None:
        # A knee MRI cannot be done in the x-ray room however free it is.
        found = diary().rooms_for("MR")
        self.assertEqual([room.code for room in found], ["MR1"])

    def test_free_slots_come_out_earliest_first(self) -> None:
        slots = list(diary().free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))

        self.assertTrue(slots)
        self.assertEqual(slots[0].starts, datetime(2026, 9, 7, 9, 0))
        self.assertEqual([s.starts for s in slots], sorted(s.starts for s in slots))

    def test_an_exam_that_would_run_past_closing_is_not_offered(self) -> None:
        # The x-ray room closes at ten. A forty-minute exam cannot start at
        # half past nine, however free the diary looks.
        slots = list(diary().free(modality="XR", minutes=40, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))

        self.assertTrue(slots)
        for slot in slots:
            self.assertLessEqual(slot.ends, datetime(2026, 9, 7, 10, 0))

    def test_nothing_in_the_past_is_ever_offered(self) -> None:
        # A slot beginning four minutes from now is the past by the time
        # anybody has said yes to it.
        now = datetime(2026, 9, 7, 10, 30)
        slots = list(diary().free(modality="MR", minutes=30, day=MONDAY, now=now))

        self.assertTrue(slots)
        for slot in slots:
            self.assertGreater(slot.starts, now)

    def test_a_day_with_no_session_has_nothing(self) -> None:
        tuesday = date(2026, 9, 8)
        self.assertEqual(
            list(diary().free(modality="MR", minutes=30, day=tuesday, now=EIGHT_THE_DAY_BEFORE)),
            [],
        )

    def test_the_same_question_twice_gets_the_same_answer(self) -> None:
        one = diary()
        first = [s.starts for s in one.free(modality="XR", minutes=15, day=MONDAY, now=EIGHT_THE_DAY_BEFORE)]
        second = [s.starts for s in one.free(modality="XR", minutes=15, day=MONDAY, now=EIGHT_THE_DAY_BEFORE)]
        self.assertEqual(first, second)


class Holding(unittest.TestCase):
    def test_a_held_slot_is_not_offered_to_the_next_caller(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        later = [s.starts for s in one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE)]
        self.assertNotIn(first.starts, later)

    def test_a_hold_that_ran_out_gives_the_slot_back(self) -> None:
        # Most conversations end by stopping. The slot has to come back on its
        # own, because nobody is going to come and tidy up.
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        much_later = EIGHT_THE_DAY_BEFORE + timedelta(hours=1)
        again = [s.starts for s in one.free(modality="MR", minutes=30, day=MONDAY, now=much_later)]
        self.assertIn(first.starts, again)

    def test_two_callers_cannot_hold_the_same_slot(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        with self.assertRaises(SlotGone):
            one.hold([first], now=EIGHT_THE_DAY_BEFORE)

    def test_an_overlapping_slot_counts_as_the_same_slot(self) -> None:
        # Half past nine for thirty minutes and a quarter to ten for thirty are
        # not the same slot, and they cannot both happen in one room.
        one = diary()
        nine = Slot(room="MR1", starts=datetime(2026, 9, 7, 9, 0), minutes=30)
        quarter_past = Slot(room="MR1", starts=datetime(2026, 9, 7, 9, 15), minutes=30)

        one.hold([nine], now=EIGHT_THE_DAY_BEFORE)
        with self.assertRaises(SlotGone):
            one.hold([quarter_past], now=EIGHT_THE_DAY_BEFORE)

    def test_the_same_time_in_another_room_is_another_slot(self) -> None:
        one = diary()
        first = Slot(room="XR1", starts=NINE, minutes=15)
        second = Slot(room="XR2", starts=NINE, minutes=15)

        one.hold([first], now=EIGHT_THE_DAY_BEFORE)
        one.hold([second], now=EIGHT_THE_DAY_BEFORE)  # does not raise

    def test_letting_go_twice_is_not_an_error(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        self.assertTrue(one.release(held.reference))
        self.assertFalse(one.release(held.reference))


class Confirming(unittest.TestCase):
    def test_a_hold_becomes_a_booking(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        booking = one.confirm(
            held.reference,
            exam_codes=["MRI-KNEE"],
            patient="A Patient",
            now=EIGHT_THE_DAY_BEFORE,
        )

        self.assertEqual(booking.slots, (first,))
        self.assertEqual(one.booking(booking.reference), booking)

    def test_a_hold_that_ran_out_cannot_be_confirmed(self) -> None:
        # It would take a slot that has been offered to somebody else since,
        # which is the exact thing holds exist to prevent.
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)

        with self.assertRaises(HoldExpired):
            one.confirm(
                held.reference,
                exam_codes=["MRI-KNEE"],
                patient="A Patient",
                now=EIGHT_THE_DAY_BEFORE + timedelta(hours=1),
            )

    def test_a_hold_that_was_let_go_cannot_be_confirmed(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)
        one.release(held.reference)

        with self.assertRaises(HoldExpired):
            one.confirm(
                held.reference, exam_codes=["X"], patient="A Patient", now=EIGHT_THE_DAY_BEFORE
            )

    def test_a_booked_slot_is_gone_for_good(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)
        one.confirm(held.reference, exam_codes=["X"], patient="A Patient", now=EIGHT_THE_DAY_BEFORE)

        much_later = EIGHT_THE_DAY_BEFORE + timedelta(hours=2)
        still = [s.starts for s in one.free(modality="MR", minutes=30, day=MONDAY, now=much_later)]
        self.assertNotIn(first.starts, still)

    def test_a_cancelled_booking_gives_the_slot_back(self) -> None:
        one = diary()
        first = next(one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE))
        held = one.hold([first], now=EIGHT_THE_DAY_BEFORE)
        booking = one.confirm(
            held.reference, exam_codes=["X"], patient="A Patient", now=EIGHT_THE_DAY_BEFORE
        )

        self.assertTrue(one.cancel(booking.reference))
        self.assertFalse(one.cancel(booking.reference))

        again = [s.starts for s in one.free(modality="MR", minutes=30, day=MONDAY, now=EIGHT_THE_DAY_BEFORE)]
        self.assertIn(first.starts, again)


class SeveralExamsAtOnce(unittest.TestCase):
    def test_two_slots_are_held_together_or_not_at_all(self) -> None:
        # Somebody booking a knee and a chest wants both or neither. Holding
        # one and failing on the other leaves them with half an appointment.
        one = diary()
        first = Slot(room="XR1", starts=NINE, minutes=15)
        second = Slot(room="XR1", starts=datetime(2026, 9, 7, 9, 30), minutes=15)
        taken = Slot(room="XR1", starts=datetime(2026, 9, 7, 9, 30), minutes=15)

        one.hold([taken], now=EIGHT_THE_DAY_BEFORE)

        with self.assertRaises(SlotGone):
            one.hold([first, second], now=EIGHT_THE_DAY_BEFORE)

        # And the first one is still free, because nothing was taken.
        free = [s.starts for s in one.free(modality="XR", minutes=15, day=MONDAY, now=EIGHT_THE_DAY_BEFORE)]
        self.assertIn(NINE, free)


class AWeek(unittest.TestCase):
    def test_the_weekend_is_closed(self) -> None:
        days = [MONDAY + timedelta(days=n) for n in range(7)]
        sessions = working_week("MR1", days)

        weekdays = {session.day.weekday() for session in sessions}
        self.assertEqual(weekdays, {0, 1, 2, 3, 4})

    def test_a_session_knows_how_long_it_is(self) -> None:
        session = Session(room="MR1", day=MONDAY, opens=time(9, 0), closes=time(12, 30))
        self.assertEqual(session.minutes(), 210)


class BuildingIt(unittest.TestCase):
    def test_a_session_in_a_room_that_does_not_exist_is_refused(self) -> None:
        # It is time that can be offered and never used.
        with self.assertRaises(ValueError):
            Diary(
                [Room(code="MR1", name="MRI", modalities=frozenset({"MR"}))],
                [Session(room="GHOST", day=MONDAY, opens=time(9, 0), closes=time(10, 0))],
            )


if __name__ == "__main__":
    unittest.main()
