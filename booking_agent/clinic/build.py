"""A clinic, from the file that describes one.

Kept apart from the things it builds so that nothing in the domain has to know
where its data came from. The tests build catalogues and diaries by hand in
three lines; this is only for running the thing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from booking_agent.clinic.catalogue import Catalogue, load
from booking_agent.clinic.diary import Diary, Room, Session

#: How far ahead the diary is laid out. Two weeks is what the agent will look
#: through before handing over, so laying out more is time nobody will offer.
WEEKS = 3


@dataclass(frozen=True)
class Clinic:
    """Everything the agent needs to know about where it is answering for."""

    name: str
    opening_hours: str
    address: str
    catalogue: Catalogue
    diary: Diary


def _time(written: str) -> time:
    hour, minute = written.split(":")
    return time(int(hour), int(minute))


def sessions_from(
    described: list[dict[str, Any]],
    *,
    starting: date,
    weeks: int = WEEKS,
) -> list[Session]:
    """The diary laid out over real days, from a weekly pattern.

    Written as a pattern rather than as dates because a clinic's week is a
    pattern; and laid out from a date passed in rather than from today, so a
    test can lay out any week it likes.
    """
    days = [starting + timedelta(days=n) for n in range(weeks * 7)]

    return [
        Session(
            room=str(entry["room"]),
            day=day,
            opens=_time(str(entry["opens"])),
            closes=_time(str(entry["closes"])),
        )
        for entry in described
        for day in days
        if day.weekday() in set(entry.get("weekdays", []))
    ]


def from_dict(described: dict[str, Any], *, starting: date) -> Clinic:
    rooms = [
        Room(
            code=str(room["code"]),
            name=str(room["name"]),
            modalities=frozenset(str(m) for m in room["modalities"]),
        )
        for room in described["rooms"]
    ]

    return Clinic(
        name=str(described.get("name", "the clinic")),
        opening_hours=str(described.get("opening_hours", "")),
        address=str(described.get("address", "")),
        catalogue=load(described["exams"]),
        diary=Diary(rooms, sessions_from(described["sessions"], starting=starting)),
    )


def from_file(path: str | Path, *, starting: date | None = None) -> Clinic:
    described = json.loads(Path(path).read_text(encoding="utf-8"))
    return from_dict(described, starting=starting or date.today())


def default(*, starting: date | None = None) -> Clinic:
    """The clinic that ships with this repository."""
    here = Path(__file__).resolve().parents[2]
    return from_file(here / "data" / "clinic.json", starting=starting)
