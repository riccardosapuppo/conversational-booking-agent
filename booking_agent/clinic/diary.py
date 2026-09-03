"""When the clinic is free, and how a slot is kept while somebody decides.

A conversation takes minutes. Between "there is a slot at nine on Tuesday" and
"yes, that one" a caller checks with their partner, finds their prescription,
asks what it costs. If nothing is holding that slot in the meantime, two people
are told about it and one of them arrives to find it gone — and they will not
find that out until the day.

So a slot offered is a slot held, and a hold expires on its own. The two rules
that follow from that are the whole of this file:

  - a hold is not a booking. It disappears if the conversation does, which is
    what conversations mostly do.
  - time is never read from the clock in here. It is passed in, so the same
    diary asked the same question twice gives the same answer, and so the
    awkward moments — a hold expiring mid-sentence, a slot that starts eight
    minutes from now — can be tested rather than waited for.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Container, Iterable, Iterator, Sequence

#: How long a slot is kept while somebody makes up their mind.
HOLD_MINUTES = 10

#: What a reference is made of.
#:
#: Not the whole alphabet, because this is read out over a telephone to
#: somebody writing it down with a pen. No O or I, which are heard as zero and
#: one; no 0, 1, 5 or 8, which are heard back as O, I, S and B; and no vowels,
#: which stops a run of random letters from occasionally spelling something the
#: clinic would rather not say out loud.
#:
#: It replaced the first twelve characters of a uuid, which was unique, correct,
#: and unusable by the person it was for.
_ALPHABET = "CDFHJKLMNPRTVWXY234679"

#: How long a reference is, before the dash. Two groups of three: people read
#: back a group of three from memory and lose their place in a group of six.
_GROUP = 3


def reference(taken: Container[str] = ()) -> str:
    """A booking reference somebody can write down while holding a phone."""
    while True:
        letters = "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP * 2))
        made = f"{letters[:_GROUP]}-{letters[_GROUP:]}"
        if made not in taken:
            return made


@dataclass(frozen=True)
class Room:
    """A room, and what can be done in it."""

    code: str
    name: str
    #: The modalities this room is equipped for. A knee MRI cannot be done in
    #: the x-ray room however free it is.
    modalities: frozenset[str]


@dataclass(frozen=True)
class Session:
    """One stretch of a day in which a room is staffed."""

    room: str
    day: date
    opens: time
    closes: time

    def minutes(self) -> int:
        start = datetime.combine(self.day, self.opens)
        end = datetime.combine(self.day, self.closes)
        return int((end - start).total_seconds() // 60)


@dataclass(frozen=True)
class Slot:
    """A time a particular room could be used, for a particular length."""

    room: str
    starts: datetime
    minutes: int

    @property
    def ends(self) -> datetime:
        return self.starts + timedelta(minutes=self.minutes)

    def overlaps(self, other: "Slot") -> bool:
        if self.room != other.room:
            return False
        return self.starts < other.ends and other.starts < self.ends


@dataclass
class Hold:
    """A slot kept for one conversation, for a little while."""

    reference: str
    slots: tuple[Slot, ...]
    until: datetime

    def alive(self, now: datetime) -> bool:
        return now < self.until


@dataclass
class Booking:
    """A slot somebody actually has."""

    reference: str
    slots: tuple[Slot, ...]
    exam_codes: tuple[str, ...]
    patient: str
    made_at: datetime


class SlotGone(Exception):
    """Raised when a slot is asked for and somebody else has it."""


class HoldExpired(Exception):
    """Raised when a hold is confirmed after its time ran out."""


def _sessions_for(sessions: Iterable[Session], room: str, day: date) -> list[Session]:
    return [s for s in sessions if s.room == room and s.day == day]


class Diary:
    """What the clinic has free, what is held, and what is booked."""

    def __init__(self, rooms: Sequence[Room], sessions: Sequence[Session]) -> None:
        self._rooms = {room.code: room for room in rooms}
        self._sessions = tuple(sessions)
        self._holds: dict[str, Hold] = {}
        self._bookings: dict[str, Booking] = {}

        unknown = {session.room for session in sessions} - set(self._rooms)
        if unknown:
            # A session in a room nobody described is time that can be offered
            # and never used.
            raise ValueError(f"sessions in rooms that do not exist: {sorted(unknown)}")

    # ---------------------------------------------------------------- looking

    @property
    def rooms(self) -> list[Room]:
        """Every room, in a fixed order.

        Here so that nothing outside has to reach into the private mapping to
        list them -- which the read-only views did until this existed, and a
        private attribute with a reader is a private attribute in name only.
        """
        return sorted(self._rooms.values(), key=lambda one: one.code)

    def rooms_for(self, modality: str) -> list[Room]:
        return [room for room in self._rooms.values() if modality in room.modalities]

    def taken(self, now: datetime) -> list[Slot]:
        """Every slot that is not free: booked, or held by somebody still here.

        Expired holds are not taken. They are the ordinary end of a
        conversation, not a state anybody has to clean up.
        """
        busy: list[Slot] = []
        for booking in self._bookings.values():
            busy.extend(booking.slots)
        for hold in self._holds.values():
            if hold.alive(now):
                busy.extend(hold.slots)
        return busy

    def free(
        self,
        *,
        modality: str,
        minutes: int,
        day: date,
        now: datetime,
        step: int = 15,
    ) -> Iterator[Slot]:
        """Every slot of this length free on this day, earliest first.

        Walked at a fixed step rather than packed end to end: a diary that only
        offers appointments back to back looks full the moment one is booked in
        the middle of a morning.
        """
        busy = self.taken(now)

        for room in sorted(self.rooms_for(modality), key=lambda r: r.code):
            for session in sorted(_sessions_for(self._sessions, room.code, day), key=lambda s: s.opens):
                start = datetime.combine(session.day, session.opens)
                closing = datetime.combine(session.day, session.closes)

                while start + timedelta(minutes=minutes) <= closing:
                    candidate = Slot(room=room.code, starts=start, minutes=minutes)

                    # Never offer the past. A slot beginning four minutes from
                    # now is the past by the time anybody says yes to it.
                    if candidate.starts > now and not any(
                        candidate.overlaps(other) for other in busy
                    ):
                        yield candidate

                    start += timedelta(minutes=step)

    # ----------------------------------------------------------------- taking

    def hold(self, slots: Sequence[Slot], now: datetime, minutes: int = HOLD_MINUTES) -> Hold:
        """Keeps these slots for one conversation.

        Raises if any of them has gone in the meantime, which between offering
        and answering is a thing that happens.
        """
        busy = self.taken(now)
        for slot in slots:
            if any(slot.overlaps(other) for other in busy):
                raise SlotGone(f"{slot.starts:%d %b %H:%M} in {slot.room} has been taken")

        # Against everything still live and everything already booked: a
        # reference short enough to read out is short enough to collide, and
        # the second caller to be given it would be handed the first one's
        # appointment.
        held = Hold(
            reference=reference({*self._holds, *self._bookings}),
            slots=tuple(slots),
            until=now + timedelta(minutes=minutes),
        )
        self._holds[held.reference] = held
        return held

    def release(self, reference: str) -> bool:
        """Gives a hold back. Doing it twice is not an error."""
        return self._holds.pop(reference, None) is not None

    def confirm(
        self,
        reference: str,
        *,
        exam_codes: Sequence[str],
        patient: str,
        now: datetime,
    ) -> Booking:
        """Turns a hold into a booking.

        The hold has to still be alive: confirming an expired one would take a
        slot that has been offered to somebody else in the meantime, which is
        the exact thing holds exist to prevent.
        """
        held = self._holds.get(reference)
        if held is None:
            raise HoldExpired("that hold is not here any more")
        if not held.alive(now):
            del self._holds[reference]
            raise HoldExpired("that hold ran out")

        # Checked again rather than trusted: a hold is only a claim on slots
        # that were free when it was made.
        busy = [slot for slot in self.taken(now) if slot not in held.slots]
        for slot in held.slots:
            if any(slot.overlaps(other) for other in busy):
                raise SlotGone(f"{slot.starts:%d %b %H:%M} in {slot.room} has been taken")

        booking = Booking(
            reference=held.reference,
            slots=held.slots,
            exam_codes=tuple(exam_codes),
            patient=patient,
            made_at=now,
        )
        self._bookings[booking.reference] = booking
        del self._holds[reference]
        return booking

    # ---------------------------------------------------------------- reading

    def booking(self, reference: str) -> Booking | None:
        return self._bookings.get(reference)

    def bookings(self) -> list[Booking]:
        return sorted(self._bookings.values(), key=lambda b: (b.slots[0].starts, b.reference))

    def cancel(self, reference: str) -> bool:
        """Gives a booking back. Doing it twice is not an error."""
        return self._bookings.pop(reference, None) is not None


def working_week(
    room: str,
    days: Iterable[date],
    opens: time = time(8, 0),
    closes: time = time(18, 0),
    *,
    closed_on: frozenset[int] = frozenset({5, 6}),
) -> list[Session]:
    """A plain week of sessions, for a diary that has to start somewhere.

    Weekends closed by default, because a demonstration diary that is open
    every day teaches whoever reads it the wrong thing about the domain.
    """
    return [
        Session(room=room, day=day, opens=opens, closes=closes)
        for day in days
        if day.weekday() not in closed_on
    ]
