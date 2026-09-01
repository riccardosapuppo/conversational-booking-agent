"""The conversation, as a graph.

The thing this replaces had one node with two and a half thousand lines inside
it, which is a way of saying it had no graph at all: every decision about what
to do next was an `if` somewhere in the middle of the same function, and there
was no way to look at the flow without reading all of it.

Here the flow is the file. Each node does one thing and says one thing; the
routing between them is a single function you can read top to bottom; and every
node can be run on a state built by hand, which is why the tests are
conversations rather than mocks.

One caller message goes in and one reply comes out. The graph is entered once
per turn rather than run to completion, because a booking conversation is not
something that finishes on its own — it waits, and waiting is the normal state.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from booking_agent.clinic.catalogue import Catalogue, Request
from booking_agent.clinic.diary import Diary, HoldExpired, Slot, SlotGone
from booking_agent.conversation.reading import Reader, Reading
from booking_agent.conversation.state import Conversation, offered_slot

#: How many turns of not being understood before a person takes over. Two, not
#: five: an agent that asks "sorry, could you say that again" three times has
#: already lost the caller, and the third time is worse than a transfer.
PATIENCE = 2

#: How many times to offer before giving up on finding something they like.
OFFERS = 3


class Turn(TypedDict, total=False):
    """One pass through the graph: what was said, and what to say back."""

    conversation: Conversation
    said: str
    reading: Reading
    reply: str
    expecting: str


def _list_slots(slots: list[Slot]) -> str:
    return "; ".join(
        f"{position}. {slot.starts:%A %-d %B at %-H:%M}" if hasattr(slot.starts, "strftime") else ""
        for position, slot in enumerate(slots, start=1)
    )


def _times(slots: list[Slot]) -> str:
    """The offered times, numbered, in the words somebody would read out."""
    lines = []
    for position, slot in enumerate(slots, start=1):
        when = slot.starts
        lines.append(f"{position}) {when.strftime('%A %d %B')} at {when.strftime('%H:%M')}")
    return "\n".join(lines)


class Agent:
    """A booking conversation, driven one turn at a time."""

    def __init__(
        self,
        *,
        catalogue: Catalogue,
        diary: Diary,
        reader: Reader,
        clinic_name: str = "the clinic",
        opening_hours: str = "Monday to Friday, 8am to 6pm",
        address: str = "12 Example Street",
    ) -> None:
        self._catalogue = catalogue
        self._diary = diary
        self._reader = reader
        self._clinic = clinic_name
        self._hours = opening_hours
        self._address = address
        self._graph = self._build()

    # ------------------------------------------------------------------ turn

    def reply_to(self, conversation: Conversation, said: str, *, now: datetime) -> str:
        """One caller message in, one agent reply out."""
        conversation.said("caller", said)

        state: Turn = {
            "conversation": conversation,
            "said": said,
            "reply": "",
            "expecting": self._expecting(conversation),
        }
        self._now = now

        result = self._graph.invoke(state)
        reply = result.get("reply", "")

        conversation.said("agent", reply)
        return reply

    def _expecting(self, conversation: Conversation) -> str:
        """What the last thing the agent said was asking for.

        The reader needs it: "two" means the second slot when times were just
        read out, and means nothing when they were not.
        """
        if conversation.stage == "offering":
            return "slot"

        # The first gap, because that is the one just asked about. Comparing
        # against the whole tuple missed the ordinary case: an exam needing
        # both a side and a contrast reports both at once, the agent asks for
        # the side, and a caller answering "left" was heard as gibberish.
        missing = conversation.missing()
        if missing and missing[0] in ("side", "contrast", "patient"):
            return missing[0]

        return ""

    # ----------------------------------------------------------------- nodes

    def _understand(self, state: Turn) -> Turn:
        reading = self._reader.read(state["said"], expecting=state.get("expecting", ""))
        return {**state, "reading": reading}

    def _handover(self, state: Turn) -> Turn:
        conversation = state["conversation"]
        reading = state["reading"]

        why = "asked_for_a_person" if reading.intent == "operator" else "not_understood"
        conversation.hand_over(why, note=conversation.summary())

        return {
            **state,
            "reply": (
                "I am putting you through to a colleague — "
                f"I have noted: {conversation.summary()}."
            ),
        }

    def _answer(self, state: Turn) -> Turn:
        return {
            **state,
            "reply": (
                f"{self._clinic} is open {self._hours}, at {self._address}. "
                "Would you like to book something?"
            ),
        }

    def _clarify(self, state: Turn) -> Turn:
        """Asks for exactly one missing thing, and says why it is being asked."""
        conversation = state["conversation"]
        reading = state["reading"]
        conversation.stage = "gathering"

        if reading.intent == "unclear":
            conversation.stalled_turns += 1
            return {**state, "reply": "Sorry — I did not catch that. What are you looking to book?"}

        conversation.stalled_turns = 0
        self._absorb(conversation, reading)

        # An exam this agent may not book. Said plainly, with the reason, and
        # handed on — not hidden so the clinic looks like it does not do it.
        blocked = conversation.unbookable()
        if blocked:
            exam = blocked[0].exam
            conversation.hand_over("cannot_be_booked_here", note=exam.name)
            return {
                **state,
                "reply": (
                    f"{exam.name} cannot be booked here: {exam.unbookable_reason}. "
                    "I am passing you to a colleague who can arrange it."
                ),
            }

        missing = conversation.missing()

        if missing == ("exam",):
            found = self._catalogue.search(reading.exam_text or state["said"])
            if len(found) > 1:
                names = " or ".join(match.exam.name for match in found[:3])
                return {**state, "reply": f"Did you mean {names}?"}
            return {**state, "reply": "What would you like to book?"}

        if "side" in missing:
            return {**state, "reply": "Left or right?"}
        if "contrast" in missing:
            return {**state, "reply": "With or without contrast?"}
        if "patient" in missing:
            return {**state, "reply": "And what name should I put it under?"}

        return {**state, "reply": "Right — let me look for a time."}

    def _offer(self, state: Turn) -> Turn:
        conversation = state["conversation"]
        self._absorb(conversation, state["reading"])

        minutes = conversation.minutes_needed()
        modality = conversation.requests[0].exam.modality

        found: list[Slot] = []
        day = self._now.date()
        for _ in range(14):
            for slot in self._diary.free(
                modality=modality, minutes=minutes, day=day, now=self._now
            ):
                found.append(slot)
                if len(found) >= OFFERS:
                    break
            if len(found) >= OFFERS:
                break
            day = day + timedelta(days=1)

        if not found:
            # Nothing in a fortnight is not something to keep looking for out
            # loud. A person can offer a waiting list; this cannot.
            conversation.hand_over("booking_failed", note="nothing free in the next two weeks")
            return {
                **state,
                "reply": (
                    "I cannot find anything in the next two weeks. "
                    "I am passing you to a colleague who can look further ahead."
                ),
            }

        conversation.offered = found
        conversation.stage = "offering"

        return {**state, "reply": "I have these:\n" + _times(found) + "\nWhich suits you?"}

    def _hold(self, state: Turn) -> Turn:
        conversation = state["conversation"]
        which = state["reading"].which or 0
        slot = offered_slot(conversation, which)

        if slot is None:
            return {
                **state,
                "reply": f"I only have {len(conversation.offered)} times — which of those?",
            }

        try:
            held = self._diary.hold([slot], now=self._now)
        except SlotGone:
            # Between reading the times out and hearing the answer, somebody
            # else took it. It is nobody's fault and it has to be said.
            conversation.offered = []
            return {
                **state,
                "reply": "That one has just gone, I am sorry. Shall I look again?",
            }

        conversation.hold = held.reference
        conversation.stage = "confirming"

        when = slot.starts
        what = conversation.summary()
        return {
            **state,
            "reply": (
                f"{what}, on {when.strftime('%A %d %B')} at {when.strftime('%H:%M')}. "
                "Shall I book that?"
            ),
        }

    def _book(self, state: Turn) -> Turn:
        conversation = state["conversation"]

        if conversation.hold is None:
            return {**state, "reply": "Let me find you a time first."}

        try:
            booking = self._diary.confirm(
                conversation.hold,
                exam_codes=[request.exam.code for request in conversation.requests],
                patient=conversation.patient or "not given",
                now=self._now,
            )
        except (HoldExpired, SlotGone):
            # The hold ran out while they were deciding, or the slot went. Both
            # end the same way for the caller, and neither is their fault.
            conversation.hold = None
            conversation.offered = []
            conversation.stage = "gathering"
            return {
                **state,
                "reply": "That time went while we were talking. Shall I look again?",
            }

        conversation.booking = booking
        conversation.hold = None
        conversation.stage = "booked"

        when = booking.slots[0].starts
        return {
            **state,
            "reply": (
                f"Booked: {conversation.summary()}, "
                f"{when.strftime('%A %d %B')} at {when.strftime('%H:%M')}. "
                f"Your reference is {booking.reference}."
            ),
        }

    def _another(self, state: Turn) -> Turn:
        """They did not like what was offered."""
        conversation = state["conversation"]

        if conversation.hold:
            self._diary.release(conversation.hold)
            conversation.hold = None

        conversation.stage = "gathering"
        return self._offer(state)

    # --------------------------------------------------------------- routing

    def _absorb(self, conversation: Conversation, reading: Reading) -> None:
        """Puts what was understood into what is known."""
        if reading.name:
            conversation.patient = reading.name

        if reading.exam_text:
            request = self._catalogue.resolve(reading.exam_text)
            if request is not None:
                conversation.requests = [request]
                return

        # A side or a contrast on its own answers the last question asked.
        if conversation.requests and (reading.side or reading.contrast is not None):
            first = conversation.requests[0]
            conversation.requests[0] = Request(
                exam=first.exam,
                side=reading.side or first.side,
                contrast=first.contrast if reading.contrast is None else reading.contrast,
            )

    def _route(self, state: Turn) -> str:
        """Where this turn goes. The whole flow of the agent, in one place."""
        conversation = state["conversation"]
        reading = state["reading"]

        if reading.intent == "operator":
            return "handover"

        if reading.intent == "unclear":
            # Asking again is fine once. Asking a third time is worse than
            # admitting it and finding somebody who can help.
            if conversation.stalled_turns >= PATIENCE:
                return "handover"
            return "clarify"

        if reading.intent == "manage":
            # Changing something already booked needs to know it is really
            # them, and this agent has no way to check. Said rather than
            # attempted.
            return "handover"

        if reading.intent == "info":
            return "answer"

        if reading.intent == "choose":
            return "hold"

        if reading.intent == "refuse":
            return "another" if conversation.offered else "clarify"

        if reading.intent == "agree":
            if conversation.stage == "confirming":
                return "book"
            if conversation.ready_to_offer and conversation.patient:
                return "offer"
            return "clarify"

        # Something was said about what they want. Whether that is enough to go
        # looking is a question for the state, not for the reader.
        self._absorb(conversation, reading)
        if conversation.unbookable():
            return "clarify"
        if conversation.missing():
            return "clarify"
        return "offer"

    def _build(self) -> Any:
        graph: StateGraph = StateGraph(Turn)

        graph.add_node("understand", self._understand)
        graph.add_node("clarify", self._clarify)
        graph.add_node("offer", self._offer)
        graph.add_node("hold", self._hold)
        graph.add_node("book", self._book)
        graph.add_node("another", self._another)
        graph.add_node("answer", self._answer)
        graph.add_node("handover", self._handover)

        graph.add_edge(START, "understand")
        graph.add_conditional_edges(
            "understand",
            self._route,
            {
                "clarify": "clarify",
                "offer": "offer",
                "hold": "hold",
                "book": "book",
                "another": "another",
                "answer": "answer",
                "handover": "handover",
            },
        )

        for node in ("clarify", "offer", "hold", "book", "another", "answer", "handover"):
            graph.add_edge(node, END)

        return graph.compile()
