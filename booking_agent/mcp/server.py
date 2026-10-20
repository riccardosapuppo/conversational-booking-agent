"""The seven tools, and the rules they do not write down a second time.

Each tool is a function of plain arguments with types on them, so the schema a
model reads is generated from the same annotations the code runs on: an enum
for a side, an enum for a reason, a date and time for a start, limits on a
count. What the types cannot say, the tools say when they refuse, in a
sentence that names what was wrong and what to do about it, because the thing
reading the refusal is a model that will try again.

Three kinds of answer, kept apart:

  a result       what was asked for, with a schema of its own
  a refusal      a ``ToolError``: the call was understood and cannot be done,
                 and the message says why. Not an error of the server.
  a fault        anything else. The model is told only that the tool failed,
                 and the details stay in this process's log.

The clock is handed in, as everywhere else that reads one, so a hold running
out is a test rather than a wait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Callable

from mcp.server.context import CallNext, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from booking_agent import __author__, __version__
from booking_agent.clinic.build import Clinic, default
from booking_agent.clinic.catalogue import Exam, Request, Side
from booking_agent.clinic.diary import (
    HOLD_MINUTES,
    LOOK_AHEAD_DAYS,
    HoldExpired,
    NotOnOffer,
    Slot,
    SlotGone,
)
from booking_agent.clinic.names import NotAName
from booking_agent.conversation.state import Escalation

#: The longest phrase a search is given. A caller can say a lot; a page of text
#: is not a request for an exam.
LONGEST_PHRASE = 300

#: The longest note a handover carries.
LONGEST_NOTE = 1000

# The arguments, typed once and used by every tool that takes them.
ExamCode = Annotated[
    str,
    Field(min_length=1, max_length=40, description="The exam's code, as search_exams returns it, e.g. MRI-KNEE."),
]
SideAsked = Annotated[
    Side | None,
    Field(description="Which side, for an exam that has one. Leave it out for an exam that has none."),
]
ContrastAsked = Annotated[
    bool | None,
    Field(description="With contrast or without, for an exam where that is a choice. Leave it out otherwise."),
]
HoldReference = Annotated[
    str,
    Field(min_length=1, max_length=20, description="The hold, as hold_slot returned it."),
]


# ------------------------------------------------------------------ results


class Room(BaseModel):
    code: str
    name: str
    modalities: list[str]


class ClinicInformation(BaseModel):
    name: str
    opening_hours: str
    address: str
    currency: str
    exams: int
    rooms: list[Room]


class ExamFound(BaseModel):
    code: str
    name: str
    modality: str
    minutes: int
    price: float
    needs_side: bool
    needs_contrast: bool
    bookable: bool
    unbookable_reason: str | None
    #: How much of what was said this exam accounts for, from 0 to 1. Two at
    #: the same score are two answers, and the caller has to be asked which.
    score: float
    matched: list[str]


class ExamsFound(BaseModel):
    asked: str
    exams: list[ExamFound]


class Time(BaseModel):
    room: str
    starts: datetime
    ends: datetime


class TimesFound(BaseModel):
    exam: str
    name: str
    minutes: int
    times: list[Time]
    #: The last day looked at. Nothing found up to it is a reason to hand over,
    #: not to keep looking.
    looked_until: date


class Held(BaseModel):
    hold: str
    until: datetime
    exam: str
    what: str
    room: str
    starts: datetime


class Booked(BaseModel):
    reference: str
    patient: str
    exam: str
    side: Side | None
    contrast: bool | None
    what: str
    room: str
    starts: datetime


class Released(BaseModel):
    hold: str
    released: bool


class HandedOver(BaseModel):
    reason: Escalation
    note: str
    released: str | None
    #: How many calls this server has handed to a person since it started.
    handed_over_so_far: int


@dataclass
class _Desk:
    """What this server remembers between calls: its holds, and its handovers.

    In memory, as the service keeps its calls, for the same reason: the booking
    is the thing worth keeping, and the diary has it.
    """

    held: dict[str, Request] = field(default_factory=dict)
    handovers: list[dict[str, Any]] = field(default_factory=list)


def _instructions(clinic: Clinic) -> str:
    return (
        f"Books diagnostic exams at {clinic.name} for a caller you are talking to. "
        "Find the exam with search_exams; when two exams score the same, ask the caller which, and never pick. "
        "Ask for the side and the contrast when an exam needs them, and never fill either in. "
        "Read the time back and keep it with hold_slot before asking the caller to confirm; "
        "call confirm_booking only after they say yes, with the patient's name as they gave it. "
        "When a tool refuses, tell the caller why in plain words, and if it names a reason to hand over, "
        "call hand_over with that reason. "
        "Call hand_over instead of booking when the caller asks for a person (asked_for_a_person); "
        "wants to change or cancel an appointment they already have (already_booked); mentions a pregnancy, "
        "kidney problems, an allergy or reaction to contrast, a pacemaker or metal in the body, or asks about "
        "their medication (needs_a_clinician); or describes anything that sounds urgent (may_be_an_emergency, "
        "and tell them to call the emergency number first)."
    )


def _request(clinic: Clinic, code: str, side: Side | None, contrast: bool | None) -> Request:
    """The request these arguments describe, or a refusal that says what is wrong with it.

    The checks are the clinic's own: whether an exam may be booked here at
    all, and what a request for it still lacks. A side for an exam that has
    none is refused rather than dropped, because a model that sends one has
    misunderstood the exam, and being quietly corrected teaches it nothing.
    """
    exam = clinic.catalogue.get(code)
    if exam is None:
        raise ToolError(f"There is no exam with the code {code!r}. search_exams finds codes from what the caller said.")

    if not exam.bookable:
        raise ToolError(
            f"{exam.name} cannot be booked here: {exam.unbookable_reason}. "
            "Tell the caller, and call hand_over with reason cannot_be_booked_here."
        )

    if side is not None and not exam.needs_side:
        raise ToolError(f"{exam.name} has no side. Leave side out.")
    if contrast is not None and not exam.needs_contrast:
        raise ToolError(f"Contrast is not a choice for {exam.name}. Leave contrast out.")

    request = Request(exam=exam, side=side, contrast=contrast)
    missing = request.missing()
    if "side" in missing:
        raise ToolError(f"{exam.name} needs a side. Ask the caller left, right or both, and do not guess.")
    if "contrast" in missing:
        raise ToolError(f"{exam.name} needs to know whether it is with contrast. Ask the caller, and do not guess.")
    return request


def _local(when: datetime | None, what: str) -> datetime | None:
    """A time as the clinic keeps them: its own local time, with no zone on it.

    Refused rather than converted. The clinic's diary has no zone, so there is
    nothing to convert a "Z" into, and comparing a zoned time with an unzoned
    one is not an answer the tool could give, only a fault it could have.
    """
    if when is not None and when.tzinfo is not None:
        raise ToolError(
            f"{what} has a time zone on it. Give it as the clinic's local time, without one, "
            "as find_slots returns them."
        )
    return when


def _found(exam: Exam, score: float, matched: frozenset[str]) -> ExamFound:
    return ExamFound(
        code=exam.code,
        name=exam.name,
        modality=exam.modality,
        minutes=exam.minutes,
        price=exam.price,
        needs_side=exam.needs_side,
        needs_contrast=exam.needs_contrast,
        bookable=exam.bookable,
        unbookable_reason=exam.unbookable_reason or None,
        score=round(score, 3),
        matched=sorted(matched),
    )


def tools_for(clinic: Clinic, *, clock: Callable[[], datetime]) -> list[Tool]:
    """The seven tools, bound to one clinic and one clock."""
    desk = _Desk()
    diary = clinic.diary

    def clinic_information() -> ClinicInformation:
        """Who the clinic is, when it is open, where it is, and which rooms it has."""
        return ClinicInformation(
            name=clinic.name,
            opening_hours=clinic.opening_hours,
            address=clinic.address,
            currency=clinic.currency,
            exams=len(clinic.catalogue),
            rooms=[Room(code=room.code, name=room.name, modalities=sorted(room.modalities)) for room in diary.rooms],
        )

    def search_exams(
        phrase: Annotated[
            str,
            Field(
                min_length=1,
                max_length=LONGEST_PHRASE,
                description="What the caller said they need, in their words, e.g. 'an MRI of my left knee'.",
            ),
        ],
    ) -> ExamsFound:
        """The exams a phrase could mean, best first: the same search the conversation uses.

        Two at the same score are an ambiguity, and the caller has to be asked
        which. None is an answer too: nothing they said names an exam here.
        An exam that cannot be booked here is listed, with the reason.
        """
        return ExamsFound(
            asked=phrase,
            exams=[_found(match.exam, match.score, match.matched) for match in clinic.catalogue.search(phrase, limit=10)],
        )

    def find_slots(
        exam: ExamCode,
        side: SideAsked = None,
        contrast: ContrastAsked = None,
        after: Annotated[
            datetime | None,
            Field(description="Only times starting after this, for a caller who wants something later."),
        ] = None,
        count: Annotated[int, Field(ge=1, le=10, description="How many times to return.")] = 3,
    ) -> TimesFound:
        """The earliest free times for one exam, over the next two weeks.

        The same times the conversation reads out. The exam has to be complete
        first: a side and a contrast where it needs them. Nothing is kept by
        this; hold_slot keeps one while the caller decides.
        """
        request = _request(clinic, exam, side, contrast)
        after = _local(after, "after")
        now = clock()
        times = diary.earliest(
            modality=request.exam.modality,
            minutes=request.exam.minutes,
            now=now,
            count=count,
            after=after,
        )
        return TimesFound(
            exam=request.exam.code,
            name=request.description,
            minutes=request.exam.minutes,
            times=[Time(room=slot.room, starts=slot.starts, ends=slot.ends) for slot in times],
            looked_until=now.date() + timedelta(days=LOOK_AHEAD_DAYS - 1),
        )

    def hold_slot(
        exam: ExamCode,
        room: Annotated[str, Field(min_length=1, max_length=20, description="The room, as find_slots returned it.")],
        starts: Annotated[datetime, Field(description="The start, as find_slots returned it.")],
        side: SideAsked = None,
        contrast: ContrastAsked = None,
    ) -> Held:
        """Keeps one time for this exam while the caller decides, for ten minutes.

        Read the time back to the caller and ask before confirm_booking. Only a
        time the diary itself would offer for this exam can be kept: the right
        room, inside its hours, on the quarter hour, and not yet started.
        """
        request = _request(clinic, exam, side, contrast)
        starts = _local(starts, "starts")
        now = clock()
        slot = Slot(room=room, starts=starts, minutes=request.exam.minutes)

        try:
            held = diary.hold([slot], now=now, modality=request.exam.modality)
        except NotOnOffer as why:
            raise ToolError(
                f"{starts:%A %d %B %H:%M} in {room} is not a time the diary offers for {request.exam.name}: {why}. "
                "find_slots lists the ones it does."
            ) from why
        except SlotGone as why:
            raise ToolError("That time has just been taken. Call find_slots again for the times still free.") from why

        desk.held[held.reference] = request
        return Held(
            hold=held.reference,
            until=held.until,
            exam=request.exam.code,
            what=request.description,
            room=slot.room,
            starts=slot.starts,
        )

    def confirm_booking(
        hold: HoldReference,
        patient: Annotated[
            str,
            Field(min_length=1, max_length=80, description="The patient's name, as the caller gave it."),
        ],
    ) -> Booked:
        """Books the time kept by a hold, for the exam it was kept for, under the patient's name.

        Only after the caller has said yes to the time read back. The exam,
        side and contrast are the ones the hold was made with and cannot be
        changed here: to change them, release_hold and start again.
        """
        request = desk.held.get(hold)
        if request is None:
            raise ToolError(
                f"No time is being kept under {hold!r} by this server. "
                "Call hold_slot first, then confirm_booking with the hold it returns."
            )

        try:
            booking = diary.confirm(hold, requests=[request], patient=patient, now=clock())
        except NotAName as why:
            raise ToolError(f"{why}. Ask the caller for the patient's name; the time is still being kept.") from why
        except HoldExpired as why:
            desk.held.pop(hold, None)
            raise ToolError(
                f"The time kept under {hold} is not kept any more: holds last {HOLD_MINUTES} minutes. "
                "Call find_slots again."
            ) from why
        except SlotGone as why:
            desk.held.pop(hold, None)
            raise ToolError("That time has been taken. Call find_slots again for the times still free.") from why

        desk.held.pop(hold, None)
        slot = booking.slots[0]
        booked = booking.requests[0]
        return Booked(
            reference=booking.reference,
            patient=booking.patient,
            exam=booked.exam.code,
            side=booked.side,
            contrast=booked.contrast,
            what=booked.description,
            room=slot.room,
            starts=slot.starts,
        )

    def release_hold(hold: HoldReference) -> Released:
        """Gives back a time being kept, for a caller who does not want it. Doing it twice is not an error."""
        desk.held.pop(hold, None)
        return Released(hold=hold, released=diary.release(hold))

    def hand_over(
        reason: Annotated[Escalation, Field(description="Why a person is needed, from the clinic's own list.")],
        note: Annotated[
            str,
            Field(
                min_length=1,
                max_length=LONGEST_NOTE,
                description="What the person picking up should read first: what the caller wants and what they said.",
            ),
        ],
        hold: Annotated[
            str | None,
            Field(max_length=20, description="A time being kept for this caller, to give back."),
        ] = None,
    ) -> HandedOver:
        """Passes the caller to a person at the clinic, with the reason and a note.

        For everything the tools refuse, and for everything listed in this
        server's instructions. There is nobody at the other end of this one:
        it records what the person would be told, and gives back the time
        named in ``hold``, which would otherwise stay kept for nobody.
        """
        released = None
        if hold is not None:
            desk.held.pop(hold, None)
            if diary.release(hold):
                released = hold

        desk.handovers.append({"reason": reason, "note": note, "at": clock()})
        return HandedOver(reason=reason, note=note, released=released, handed_over_so_far=len(desk.handovers))

    looking = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    keeping = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
    giving_back = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

    return [
        Tool.from_function(clinic_information, title="The clinic", annotations=looking),
        Tool.from_function(search_exams, title="Find an exam", annotations=looking),
        Tool.from_function(find_slots, title="Free times for an exam", annotations=looking),
        Tool.from_function(hold_slot, title="Keep a time", annotations=keeping),
        Tool.from_function(confirm_booking, title="Book the time kept", annotations=keeping),
        Tool.from_function(release_hold, title="Give a time back", annotations=giving_back),
        Tool.from_function(hand_over, title="Fetch a person", annotations=keeping),
    ]


class OnlyTheArgumentsItTakes:
    """Refuses a call that names an argument the tool does not have.

    The arguments are checked against their types, and nothing checked their
    names: an argument the tool does not take was dropped without a word. That
    is not leniency. A model that writes ``contrats`` for ``contrast`` gets an
    exam booked with the contrast left out of it, and no sign anything went
    wrong, so the schema says no extra arguments and this says it out loud.
    """

    def __init__(self, tools: list[Tool]) -> None:
        self._takes = {tool.name: frozenset(tool.parameters.get("properties", {})) for tool in tools}

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext):
        if ctx.method == "tools/call" and isinstance(ctx.params, dict):
            name = ctx.params.get("name")
            given = ctx.params.get("arguments") or {}
            takes = self._takes.get(name) if isinstance(name, str) else None
            if takes is not None and isinstance(given, dict):
                unknown = sorted(set(given) - takes)
                if unknown:
                    said = ", ".join(unknown)
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"{name} takes no argument called {said}. It takes: {', '.join(sorted(takes))}.",
                            )
                        ],
                        is_error=True,
                    )
        return await call_next(ctx)


def build(clinic: Clinic | None = None, *, clock: Callable[[], datetime] = datetime.now) -> MCPServer:
    """The server, for one clinic, with everything it depends on handed to it."""
    where = clinic or default()
    tools = tools_for(where, clock=clock)

    # Said in the schema as well as enforced, so a model reading it knows.
    for tool in tools:
        tool.parameters["additionalProperties"] = False

    return MCPServer(
        name="clinic-booking",
        title=f"{where.name} bookings",
        description=f"Book diagnostic exams at {where.name}. Developed by {__author__}.",
        instructions=_instructions(where),
        version=__version__,
        tools=tools,
        middleware=[OnlyTheArgumentsItTakes(tools)],
        log_level="WARNING",
    )

