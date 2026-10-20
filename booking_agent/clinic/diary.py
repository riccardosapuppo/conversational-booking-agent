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

from booking_agent.clinic.catalogue import Request
from booking_agent.clinic.names import NotAName, why_not_a_name

#: How long a slot is kept while somebody makes up their mind.
HOLD_MINUTES = 10

#: How far ahead anything is looked for before a person is fetched instead.
#: A fortnight: nothing free in two weeks is not something to keep looking for
#: out loud, and a person can offer a waiting list.
LOOK_AHEAD_DAYS = 14

#: The step times are offered at, from the moment a room opens.
STEP_MINUTES = 15

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
    """A slot somebody actually has, and what it is for.

    What it is for is the request, side and contrast included, and not the
    code of an exam. It used to be the code: the caller was told "MRI knee left
    without contrast", the diary wrote down MRI-KNEE, and whoever opened the
    diary on the day had no way to know which knee. The one detail a booking
    agent exists to get right was the one detail it threw away.
    """

    reference: str
    slots: tuple[Slot, ...]
    requests: tuple[Request, ...]
    patient: str
    made_at: datetime

    @property
    def exam_codes(self) -> tuple[str, ...]:
        return tuple(request.exam.code for request in self.requests)


class SlotGone(Exception):
    """Raised when a slot is asked for and somebody else has it."""


class HoldExpired(Exception):
    """Raised when a hold is confirmed after its time ran out."""


class NotOnOffer(Exception):
    """Raised when a slot is asked for that this diary would never have offered.

    Not the same as gone. Gone is a time that was free and has been taken;
    this is a time that was never there to take: in a room that does not do
    the exam, outside its hours, off the quarter hours the diary works in, or
    already in the past.
    """


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
        step: int = STEP_MINUTES,
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

    def earliest(
        self,
        *,
        modality: str,
        minutes: int,
        now: datetime,
        count: int,
        after: datetime | None = None,
        skip: Container[tuple[str, datetime]] = (),
    ) -> list[Slot]:
        """The first free times of this length, over the next fortnight.

        What the conversation reads out and what the server hands back are
        this one list. ``skip`` is the times already turned down, as room and
        start, because the diary hands out a fresh Slot every time it is asked
        and two of them are never the same object; ``after`` is for somebody
        who wants something later than what they have heard.
        """
        found: list[Slot] = []
        day = now.date()

        for _ in range(LOOK_AHEAD_DAYS):
            for slot in self.free(modality=modality, minutes=minutes, day=day, now=now):
                if (slot.room, slot.starts) in skip:
                    continue
                if after is not None and slot.starts <= after:
                    continue
                found.append(slot)
                if len(found) >= count:
                    return found
            day += timedelta(days=1)

        return found

    def on_offer(self, slot: Slot, *, modality: str, now: datetime, step: int = STEP_MINUTES) -> str:
        """Why this is not a time the diary offers for this modality; empty if it is.

        The same answer ``free`` gives, asked of one slot rather than walked
        out of a day, and without the question of whether it is taken: that is
        ``SlotGone``, and a different thing to tell somebody.
        """
        room = self._rooms.get(slot.room)
        if room is None:
            return f"there is no room {slot.room}"
        if modality not in room.modalities:
            return f"{room.name} does not do {modality}"
        if slot.starts <= now:
            return "it has already started"

        for session in _sessions_for(self._sessions, slot.room, slot.starts.date()):
            opens = datetime.combine(session.day, session.opens)
            closes = datetime.combine(session.day, session.closes)
            if opens <= slot.starts and slot.ends <= closes:
                if (slot.starts - opens) % timedelta(minutes=step):
                    return f"times in {room.name} start every {step} minutes from {session.opens:%H:%M}"
                return ""

        return f"{room.name} is not open for {slot.minutes} minutes from {slot.starts:%H:%M} on {slot.starts:%A %d %B}"

    # ----------------------------------------------------------------- taking

    def hold(
        self,
        slots: Sequence[Slot],
        now: datetime,
        minutes: int = HOLD_MINUTES,
        *,
        modality: str | None = None,
    ) -> Hold:
        """Keeps these slots for one conversation.

        Raises if any of them has gone in the meantime, which between offering
        and answering is a thing that happens.

        With ``modality``, raises as well for a slot this diary would never
        have offered for it. The conversation only ever holds what it was
        offered, so it never meets that; a second way in can be handed any
        time at all, and is told so here rather than trusted to have checked.
        """
        if modality is not None:
            for slot in slots:
                wrong = self.on_offer(slot, modality=modality, now=now)
                if wrong:
                    raise NotOnOffer(wrong)

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
        requests: Sequence[Request],
        patient: str,
        now: datetime,
    ) -> Booking:
        """Turns a hold into a booking.

        The hold has to still be alive: confirming an expired one would take a
        slot that has been offered to somebody else in the meantime, which is
        the exact thing holds exist to prevent.
        """
        if not requests:
            # A booking for nothing is a slot taken from everybody else for no
            # reason anybody could read off the diary afterwards.
            raise ValueError("a booking has to be for something")

        # Refused here, where every way into the diary passes, and not only by
        # whoever happened to ask first.
        wrong = why_not_a_name(patient)
        if wrong:
            raise NotAName(f"{patient!r} cannot be booked under: {wrong}")

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
            requests=tuple(requests),
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
