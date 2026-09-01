"""Building a clinic out of the file that describes one.

The interesting part is not that the fields are read — it is the week. A clinic
keeps its diary as a pattern ("the MRI room, weekday mornings"), and everything
downstream works in real days with real dates on them. That translation is
where an off-by-one puts somebody in a room on a Sunday.

The clinic that ships with the repository is tested here too, because it is
loaded by the demonstration, by the transcripts and by the service, and a
typo in it fails all three at once.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta

from booking_agent.clinic.build import default, from_dict, sessions_from


MONDAY = date(2026, 9, 7)
BEFORE = datetime(2026, 9, 4, 9, 0)


class LayingOutTheWeek(unittest.TestCase):
    def test_a_weekly_pattern_becomes_real_days(self) -> None:
        sessions = sessions_from(
            [{"room": "MR1", "opens": "09:00", "closes": "13:00", "weekdays": [0]}],
            starting=MONDAY,
            weeks=2,
        )

        self.assertEqual([session.day for session in sessions], [MONDAY, date(2026, 9, 14)])
        self.assertEqual(sessions[0].opens, time(9, 0))
        self.assertEqual(sessions[0].closes, time(13, 0))

    def test_every_day_is_the_weekday_it_was_asked_for(self) -> None:
        # Monday is 0 and Sunday is 6, and getting that wrong is a room that
        # opens on the wrong day for the life of the clinic.
        for weekday in range(7):
            with self.subTest(weekday=weekday):
                sessions = sessions_from(
                    [{"room": "R", "opens": "09:00", "closes": "10:00", "weekdays": [weekday]}],
                    starting=MONDAY,
                    weeks=3,
                )
                self.assertEqual(len(sessions), 3)
                self.assertTrue(all(session.day.weekday() == weekday for session in sessions))

    def test_starting_on_a_day_that_is_not_a_monday(self) -> None:
        # The three weeks run from the day given, not from the Monday of its
        # week: a clinic laid out on a Wednesday should not be offering the two
        # days before it.
        wednesday = date(2026, 9, 9)
        sessions = sessions_from(
            [{"room": "R", "opens": "09:00", "closes": "10:00", "weekdays": [0, 1, 2, 3, 4]}],
            starting=wednesday,
            weeks=1,
        )

        self.assertTrue(all(session.day >= wednesday for session in sessions))

    def test_a_pattern_with_no_days_lays_out_nothing(self) -> None:
        sessions = sessions_from(
            [{"room": "R", "opens": "09:00", "closes": "10:00", "weekdays": []}],
            starting=MONDAY,
        )
        self.assertEqual(sessions, [])


class ReadingADescription(unittest.TestCase):
    def described(self) -> dict:
        return {
            "name": "Somewhere",
            "opening_hours": "always",
            "address": "nowhere",
            "exams": [
                {
                    "code": "XR-CHEST",
                    "name": "X-ray chest",
                    "modality": "XR",
                    "minutes": 10,
                    "price": 40.0,
                }
            ],
            "rooms": [{"code": "XR1", "name": "X-ray room", "modalities": ["XR"]}],
            "sessions": [
                {"room": "XR1", "opens": "08:00", "closes": "18:00", "weekdays": [0, 1, 2, 3, 4]}
            ],
        }

    def test_what_it_builds(self) -> None:
        clinic = from_dict(self.described(), starting=MONDAY)

        self.assertEqual(clinic.name, "Somewhere")
        self.assertEqual(len(clinic.catalogue), 1)
        self.assertEqual(clinic.catalogue.get("XR-CHEST").name, "X-ray chest")

    def test_the_rooms_reach_the_diary(self) -> None:
        clinic = from_dict(self.described(), starting=MONDAY)

        free = clinic.diary.free(modality="XR", minutes=10, day=MONDAY, now=BEFORE)
        self.assertTrue(free)
        self.assertTrue(all(slot.room == "XR1" for slot in free))

    def test_a_missing_exam_field_is_not_swallowed(self) -> None:
        # A clinic that half-loads is worse than one that will not load: the
        # exam nobody notices is missing is the one somebody rings about.
        described = self.described()
        del described["exams"][0]["minutes"]

        with self.assertRaises(KeyError):
            from_dict(described, starting=MONDAY)


class TheClinicThatShipsWithThis(unittest.TestCase):
    def test_it_loads(self) -> None:
        clinic = default(starting=MONDAY)

        self.assertTrue(clinic.name)
        self.assertTrue(len(clinic.catalogue) >= 5)
        self.assertTrue(clinic.opening_hours)
        self.assertTrue(clinic.address)

    def test_every_exam_has_a_room_that_could_do_it(self) -> None:
        # An exam in a modality no room performs can be asked for, understood,
        # and then never offered a time — the caller is told the clinic has
        # nothing free for a fortnight, which is not what happened.
        clinic = default(starting=MONDAY)

        for exam in clinic.catalogue:
            with self.subTest(exam=exam.code):
                self.assertTrue(clinic.diary.rooms_for(exam.modality))

    def test_every_exam_can_actually_be_offered_a_time(self) -> None:
        # A room that does it is not enough: the room has to be open for long
        # enough. The spine MRI is deliberately longer than some of the
        # sessions, and an exam that fits in none of them is not an awkward
        # case to demonstrate — it is an exam nobody can book.
        clinic = default(starting=MONDAY)
        fortnight = [MONDAY + timedelta(days=n) for n in range(14)]

        for exam in clinic.catalogue:
            if not exam.bookable:
                continue
            with self.subTest(exam=exam.code):
                fits = any(
                    clinic.diary.free(
                        modality=exam.modality, minutes=exam.minutes, day=day, now=BEFORE
                    )
                    for day in fortnight
                )
                self.assertTrue(fits)

    def test_the_awkward_cases_are_still_awkward(self) -> None:
        # This clinic earns its keep by being difficult. If somebody tidies it
        # up, the cases the agent is meant to be good at stop being exercised
        # and the transcripts go quiet without anything having improved.
        clinic = default(starting=MONDAY)

        knees = [exam for exam in clinic.catalogue if "knee" in exam.name.lower()]
        self.assertGreater(len(knees), 1, "one word should still name two exams")

        self.assertTrue(
            any(exam.needs_side and exam.needs_contrast for exam in clinic.catalogue),
            "something should still need asking about twice",
        )
        self.assertTrue(
            any(not exam.bookable for exam in clinic.catalogue),
            "something should still be beyond what the agent may book",
        )


if __name__ == "__main__":
    unittest.main()
