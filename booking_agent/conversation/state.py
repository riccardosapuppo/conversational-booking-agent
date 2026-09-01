"""What the agent knows so far, and nothing about how it found out.

A booking conversation is not a form. The parts arrive in whatever order the
caller says them, some of them twice and some contradicting the last thing they
said, and the agent has to be able to say at any moment what it has, what it is
still missing, and whether it is allowed to go ahead.

That is all this file is: the state, and the questions you can ask it. Nothing
here talks to a model, a database or a telephone — which is what makes the rest
of the agent testable, because every interesting situation can be built by hand
in three lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Sequence

from booking_agent.clinic.catalogue import Exam, Request
from booking_agent.clinic.diary import Booking, Slot

#: Why a conversation was handed to a person. Kept as a closed set rather than
#: free text: "the agent gave up" is not a reason anybody can act on, and these
#: are the things a supervisor actually wants counted at the end of a week.
Escalation = Literal[
    "asked_for_a_person",
    "not_understood",
    "cannot_be_booked_here",
    "booking_failed",
    "caller_is_upset",
    #: Something already in the diary. Its own reason rather than one of the
    #: others: the agent understood perfectly well and still cannot help,
    #: because it has no way to know the caller is who they say they are.
    "already_booked",
]

Stage = Literal[
    "greeting",
    "gathering",
    "offering",
    "confirming",
    "booked",
    "handed_over",
    "closed",
]


@dataclass
class Message:
    """One thing that was said, by one side."""

    who: Literal["caller", "agent"]
    text: str


@dataclass
class Conversation:
    """Everything one caller has told the agent, and where they have got to."""

    session: str
    stage: Stage = "greeting"
    messages: list[Message] = field(default_factory=list)

    #: What they want done. More than one because people book a knee and a
    #: chest in the same call, and the second is not a correction of the first.
    requests: list[Request] = field(default_factory=list)

    #: Who it is for. Often not the person speaking: somebody rings for their
    #: mother, and the number on the line is not the patient's.
    patient: str | None = None
    booking_for_someone_else: bool = False

    #: The exams just read out as a question, when what they said named more
    #: than one. Kept because the answer only makes sense against them: "the
    #: MRI one" is not a search of the catalogue, it is a choice between two
    #: things the agent said out loud a moment ago, and an agent that forgets
    #: what it just asked asks it again.
    candidates: list[Exam] = field(default_factory=list)

    #: What was offered last, so "the second one" means something.
    offered: list[Slot] = field(default_factory=list)

    #: Times already turned down. Offering them again is how "no, something
    #: else" turns into the same three times for ever.
    declined: list[Slot] = field(default_factory=list)

    #: The slots being kept while they decide, if any.
    hold: str | None = None

    booking: Booking | None = None

    handed_over: Escalation | None = None
    handover_note: str = ""

    #: How many turns have gone by without progress. An agent that asks the
    #: same question four times is one a person should have taken over from.
    stalled_turns: int = 0

    def said(self, who: Literal["caller", "agent"], text: str) -> None:
        self.messages.append(Message(who=who, text=text))

    def last_from_caller(self) -> str:
        for message in reversed(self.messages):
            if message.who == "caller":
                return message.text
        return ""

    # ------------------------------------------------------------- questions

    def missing(self) -> tuple[str, ...]:
        """Everything still needed before a booking can be offered.

        In the order it should be asked: what, then who. Asking for a name
        before knowing whether the clinic even does the exam wastes the one
        thing a caller notices, which is their own time.
        """
        if not self.requests:
            return ("exam",)

        for request in self.requests:
            gaps = request.missing()
            if gaps:
                return gaps

        if not self.patient:
            return ("patient",)

        return ()

    @property
    def ready_to_offer(self) -> bool:
        """Whether the agent knows enough to go looking for a time."""
        return bool(self.requests) and all(request.complete for request in self.requests)

    @property
    def ready_to_book(self) -> bool:
        return self.ready_to_offer and bool(self.patient) and self.hold is not None

    @property
    def over(self) -> bool:
        return self.stage in ("booked", "handed_over", "closed")

    def unbookable(self) -> list[Request]:
        """The things asked for that this agent is not allowed to book."""
        return [request for request in self.requests if not request.exam.bookable]

    def minutes_needed(self) -> int:
        return sum(request.exam.minutes for request in self.requests)

    def hand_over(self, why: Escalation, note: str = "") -> None:
        """Gives the conversation to a person, and says why.

        The reason is recorded even when the caller asked outright, because
        "they asked" and "it could not be understood" mean very different
        things to whoever reads the week's numbers.
        """
        self.stage = "handed_over"
        self.handed_over = why
        self.handover_note = note

    def summary(self) -> str:
        """One line a person taking over can read before they say hello."""
        if not self.requests:
            wanted = "nothing yet"
        else:
            wanted = ", ".join(
                _describe(request) for request in self.requests
            )

        who = self.patient or "name not given"
        return f"{who}: {wanted}"


def _describe(request: Request) -> str:
    parts = [request.exam.name]
    if request.side:
        parts.append(request.side)
    if request.contrast is True:
        parts.append("with contrast")
    elif request.contrast is False:
        parts.append("without contrast")
    return " ".join(parts)


def offered_slot(conversation: Conversation, which: int) -> Slot | None:
    """The slot a caller meant by "the second one".

    Counted from one, because that is how it was read out to them. Off by one
    here is somebody arriving on the wrong day.
    """
    if which < 1 or which > len(conversation.offered):
        return None
    return conversation.offered[which - 1]


def chosen_exam(conversation: Conversation, which: int) -> Exam | None:
    """The exam a caller meant by "the second one", out of the ones just named.

    The same counting-from-one as the slots, and the same reason for being a
    function rather than an index: off by one here books the wrong exam.
    """
    if which < 1 or which > len(conversation.candidates):
        return None
    return conversation.candidates[which - 1]


def new(session: str, *, now: datetime | None = None) -> Conversation:
    """A conversation that has not started yet."""
    del now  # kept in the signature: the clock belongs to the caller, not here
    return Conversation(session=session)


def with_requests(session: str, requests: Sequence[Request]) -> Conversation:
    """A conversation already part-way through, for tests and for resuming."""
    conversation = Conversation(session=session, stage="gathering")
    conversation.requests = list(requests)
    return conversation
