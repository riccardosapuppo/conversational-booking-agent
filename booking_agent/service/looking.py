"""What the clinic looks like from outside: the catalogue, the diary, the book.

Read-only, all of it. Nothing here holds a slot, books one or moves a call on —
those belong to the conversation, which is where the rules that guard them live.
A screen that could book directly would be a second way to do the one thing this
project is about, and the second way is always the one nobody tested.

These exist because the agent was only ever reachable through a terminal. That
made it invisible: somebody who wanted to see whether it works had to install
Python, read a README and type at a prompt, and most people who might want to
see it will not do any of that. The console this feeds is the same agent, over
the same endpoints, with the clinic drawn beside it — so what the agent says can
be checked against what the diary actually holds.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from fastapi import FastAPI

from booking_agent.clinic.build import Clinic


def attach(api: FastAPI, where: Clinic, clock: Callable[[], datetime]) -> None:
    """Adds the read-only views of the clinic to an application."""

    @api.get("/clinic", tags=["looking"])
    def clinic() -> dict:
        """Who this is, in the words the agent uses on the telephone."""
        return {
            "name": where.name,
            "opening_hours": where.opening_hours,
            "address": where.address,
            "exams": len(where.catalogue),
            "rooms": [
                {"code": room.code, "name": room.name, "modalities": sorted(room.modalities)}
                for room in where.diary.rooms
            ],
        }

    @api.get("/catalogue", tags=["looking"])
    def catalogue(q: str = "") -> dict:
        """Every exam, or the ones a phrase finds.

        With `q`, this is the **same search the agent uses** — not a filter
        written for the screen. Two searches over one catalogue would be two
        things to keep in step, and the screen's copy would be the one that
        quietly stopped agreeing with what the agent actually does.
        """
        if q.strip():
            found = [
                {**as_exam(match.exam), "matched": sorted(match.matched), "score": round(match.score, 3)}
                for match in where.catalogue.search(q, limit=20)
            ]
            return {"asked": q, "exams": found, "searched": True}

        return {
            "asked": q,
            "exams": [as_exam(exam) for exam in sorted(where.catalogue, key=lambda one: (one.modality, one.name))],
            "searched": False,
        }

    @api.get("/diary", tags=["looking"])
    def diary(days: int = 5, minutes: int = 30) -> dict:
        """What is free, by day and by room, for the next few days.

        `minutes` matters and is not a detail: a diary is only free *for
        something*. Thirty minutes free does not mean an exam needing forty-five
        can be booked there, and a screen that showed "free" without a length
        would be showing a number nobody can act on.
        """
        now = clock()
        days = max(1, min(days, 14))
        minutes = max(5, min(minutes, 240))

        taken = where.diary.taken(now)
        modalities = sorted({modality for room in where.diary.rooms for modality in room.modalities})

        out = []

        for ahead in range(days):
            when = (now + timedelta(days=ahead)).date()
            free_here: dict[str, list[str]] = {}

            for modality in modalities:
                slots = list(where.diary.free(modality=modality, minutes=minutes, day=when, now=now))
                for slot in slots:
                    free_here.setdefault(slot.room, []).append(slot.starts.isoformat(timespec="minutes"))

            out.append(
                {
                    "day": when.isoformat(),
                    "weekday": when.strftime("%A"),
                    "rooms": [
                        {"room": room, "free": times[:40], "more": max(0, len(times) - 40)}
                        for room, times in sorted(free_here.items())
                    ],
                }
            )

        return {
            "now": now.isoformat(timespec="seconds"),
            "for_minutes": minutes,
            "days": out,
            # Held and booked together: from a diary's point of view they are
            # the same thing, which is time that cannot be offered. What tells
            # them apart is how long they last, and that is in /bookings.
            "not_free": [
                {"room": slot.room, "starts": slot.starts.isoformat(timespec="minutes"), "minutes": slot.minutes}
                for slot in sorted(taken, key=lambda one: one.starts)
            ],
        }

    @api.get("/bookings", tags=["looking"])
    def bookings() -> dict:
        """What the agent has actually booked, this run.

        In memory, like everything else here: this clinic does not exist and
        nothing about it is worth keeping between two runs of a demonstration.
        """
        return {
            "bookings": [
                {
                    "reference": booking.reference,
                    "patient": booking.patient,
                    "exams": list(booking.exam_codes),
                    "starts": booking.slots[0].starts.isoformat(timespec="minutes"),
                    "minutes": sum(slot.minutes for slot in booking.slots),
                    "room": booking.slots[0].room,
                }
                for booking in sorted(where.diary.bookings(), key=lambda one: one.slots[0].starts)
            ]
        }


def as_exam(exam) -> dict:
    """One exam, as a screen needs it.

    `bookable` and its reason travel together, always. An exam the agent may not
    book is not hidden — hiding it would make it look like something the clinic
    does not do, when it is something a person has to arrange — so the screen
    has to be able to say which, and why.
    """
    return {
        "code": exam.code,
        "name": exam.name,
        "modality": exam.modality,
        "minutes": exam.minutes,
        "price": exam.price,
        "synonyms": list(exam.synonyms),
        "needs_side": exam.needs_side,
        "needs_contrast": exam.needs_contrast,
        "bookable": exam.bookable,
        "unbookable_reason": exam.unbookable_reason or None,
    }
