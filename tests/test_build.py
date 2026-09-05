"""Building a clinic out of the file that describes one.

The interesting part is not that the fields are read — it is the week. A clinic
keeps its diary as a pattern ("the MRI room, weekday mornings"), and everything
downstream works in real days with real dates on them. That translation is
where an off-by-one puts somebody in a room on a Sunday.

The clinic that ships with the repository is tested here too, because it is
loaded by the demonstration, by the transcripts and by the service, and a
typo in it fails all three at once. So is the paragraph the README writes
about it: a figure quoted in prose has nothing holding it to the file it was
measured from, and this one had drifted.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from booking_agent.clinic.build import default, from_dict, sessions_from
from booking_agent.clinic.diary import Slot


MONDAY = date(2026, 9, 7)
BEFORE = datetime(2026, 9, 4, 9, 0)

HERE = Path(__file__).resolve().parents[1]

#: The sentence in the README that quotes figures measured out of this clinic.
#:
#: Matched against the prose with its line breaks flattened, so rewrapping the
#: paragraph costs nothing and rewording the claim costs a failure. That is why
#: it is one pattern rather than four loose numbers: a check that still matches
#: after the sentence has changed is a check of nothing, and these are numbers
#: that go stale without anybody touching them.
QUOTED = re.compile(
    r"the MRI room is open \*\*(?P<opens>\d\d:\d\d) to (?P<closes>\d\d:\d\d)\*\* "
    r"on a Monday, one unbroken stretch, and across it the diary offers "
    r"the \*\*(?P<knee_minutes>\d+)\*\*-minute knee scan "
    r"\*\*(?P<knee_starts>\d+)\*\* start times and "
    r"the \*\*(?P<spine_minutes>\d+)\*\*-minute whole spine "
    r"\*\*(?P<spine_starts>\d+)\*\*\."
)


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
        # enough, and open that long before it closes. The spine MRI is the
        # longest thing in the catalogue and loses the end of every session to
        # that, which is the awkward case worth having. An exam that outgrew a
        # session outright would not be an awkward case at all — it would be
        # an exam nobody can book.
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


class TheFiguresTheReadmeQuotes(unittest.TestCase):
    """The README's paragraph about this clinic, worked out again from it.

    Its figures were true the day they were typed, which is the whole trouble:
    prose cannot notice that a session moved or an exam grew, and the paragraph
    that says "measured" is the one nobody measures twice. This one said the
    spine MRI was longer than some of the sessions in the diary until somebody
    counted — seventy-five minutes against a shortest session of three hours,
    which no version of this file has ever been close to.

    So the numbers are read back out of the README and worked out here. The
    claims either side of them are checked too — one room, one unbroken stretch
    of it — because a count is only as honest as the sentence carrying it.
    """

    def setUp(self) -> None:
        self.clinic = default(starting=MONDAY)

    def quoted(self) -> dict[str, str]:
        prose = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
        found = QUOTED.search(prose)

        assert found is not None, (
            "the README no longer contains the sentence these figures belong to: "
            "read what it claims now before loosening this pattern, because the "
            "reason it exists is that the sentence before it was wrong."
        )
        return found.groupdict()

    def offered(self, minutes: int) -> list[Slot]:
        """Every start the MRI room has on the Monday, for something this long."""
        return list(
            self.clinic.diary.free(modality="MR", minutes=minutes, day=MONDAY, now=BEFORE)
        )

    def test_the_room_is_open_when_the_readme_says_it_is(self) -> None:
        # Taken from the diary rather than from the sessions in the file: what
        # the sentence promises a reader is time that could be offered, and a
        # session nothing is ever offered in would still be in the file.
        quoted = self.quoted()
        quarters = self.offered(15)

        self.assertEqual(
            {slot.room for slot in quarters}, {"MR1"}, "the README says the MRI room, singular"
        )
        self.assertEqual(quarters[0].starts.strftime("%H:%M"), quoted["opens"])
        self.assertEqual(quarters[-1].ends.strftime("%H:%M"), quoted["closes"])

        # "One unbroken stretch". A gap in the middle leaves the opening and
        # the closing time both correct and the sentence between them false,
        # which is the shape of failure this class is here for.
        self.assertEqual(
            [
                after.starts
                for before, after in zip(quarters, quarters[1:])
                if after.starts != before.starts + timedelta(minutes=15)
            ],
            [],
            "the MRI room's Monday is not one stretch any more",
        )

    def test_the_counts_are_what_the_diary_would_offer(self) -> None:
        quoted = self.quoted()

        for code, minutes, starts in (
            ("MRI-KNEE", "knee_minutes", "knee_starts"),
            ("MRI-SPINE", "spine_minutes", "spine_starts"),
        ):
            exam = self.clinic.catalogue.get(code)
            offered = len(self.offered(exam.minutes))
            with self.subTest(exam=code):
                self.assertEqual(
                    str(exam.minutes),
                    quoted[minutes],
                    f"the README calls {code} a {quoted[minutes]}-minute exam; "
                    f"data/clinic.json says {exam.minutes}",
                )
                self.assertEqual(
                    offered,
                    int(quoted[starts]),
                    f"the README says {code} is offered {quoted[starts]} start times "
                    f"on the Monday; the diary offers {offered}",
                )

    def test_noon_is_free_and_will_not_take_the_spine(self) -> None:
        # The line the two counts are there to support, and the reason length
        # is a control on the console rather than an assumption: the room is
        # free at noon, and free is not the same as free for long enough.
        noon = datetime.combine(MONDAY, time(12, 0))
        knee = self.clinic.catalogue.get("MRI-KNEE")
        spine = self.clinic.catalogue.get("MRI-SPINE")

        self.assertIn(noon, [slot.starts for slot in self.offered(knee.minutes)])
        self.assertNotIn(noon, [slot.starts for slot in self.offered(spine.minutes)])

    def test_the_note_in_the_file_says_no_more_than_this(self) -> None:
        # data/clinic.json makes the same claim in prose, in the `note` beside
        # the length it rests on, and nothing loads that field: it is dropped
        # on the way into an Exam and read only by whoever opens the file. It
        # was the second of the three places the old sentence was wrong in, and
        # the three agreeing with one another was what made it look measured.
        described = json.loads((HERE / "data" / "clinic.json").read_text(encoding="utf-8"))
        note = next(row["note"] for row in described["exams"] if row["code"] == "MRI-SPINE")
        spine = self.clinic.catalogue.get("MRI-SPINE")
        stingiest = len(self.offered(spine.minutes))

        self.assertIn(
            "longest", note.lower(), "the note has been reworded past what is checked here"
        )
        self.assertEqual(spine.minutes, max(exam.minutes for exam in self.clinic.catalogue))

        against = {
            exam.code: len(self.offered(exam.minutes))
            for exam in self.clinic.catalogue
            if exam.modality == spine.modality and exam.code != spine.code
        }
        self.assertTrue(
            all(stingiest < count for count in against.values()),
            f"the spine gets {stingiest} starts on the Monday against {against}: "
            "it is not the one its room is stingiest with any more",
        )


if __name__ == "__main__":
    unittest.main()
