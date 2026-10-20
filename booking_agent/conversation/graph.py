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
from booking_agent.clinic.names import plausible_name
from booking_agent.conversation.reading import Reader, Reading
from booking_agent.conversation.state import Conversation, chosen_exam, offered_slot

#: How many turns of not being understood before a person takes over. Two, not
#: five: an agent that asks "sorry, could you say that again" three times has
#: already lost the caller, and the third time is worse than a transfer.
PATIENCE = 2

#: How many times are read out at once. Three: a listener holds three in their
#: head, and a fourth pushes the first one out.
OFFERS = 3

#: How many sets of times can be turned down before a person takes over.
#:
#: The line above said this was what OFFERS meant, and the README said three
#: sets nobody likes end with a person and a reason. Neither was true: OFFERS
#: was only ever the length of one set, and "no, something else" was answered
#: with another set for as long as the diary had times in it. The golden calls
#: asked the question, and this is the answer they were promised.
SETS = 3


class Turn(TypedDict, total=False):
    """One pass through the graph: what was said, and what to say back."""

    conversation: Conversation
    said: str
    reading: Reading
    reply: str
    expecting: str


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
        address: str = "14 Mulberry Lane",
        currency: str = "",
    ) -> None:
        self._catalogue = catalogue
        self._diary = diary
        self._reader = reader
        self._clinic = clinic_name
        self._hours = opening_hours
        self._address = address
        self._currency = currency
        self._graph = self._build()

    # ------------------------------------------------------------------ turn

    def drawn(self):
        """The graph itself, for anything that wants to draw or inspect it.

        A method rather than reaching into the private attribute, so that the
        one tool that draws the README picture is not the reason this class
        can never change how it holds its graph.
        """
        return self._graph.get_graph()

    def greeting(self) -> str:
        """What it says before anybody has said anything.

        Here rather than in whatever starts the call, because it is a thing the
        agent says, and everything the agent says is written in one place. The
        alternative puts the first sentence of every conversation somewhere the
        conversation tests cannot see it.
        """
        return f"You are through to {self._clinic}. What can I book for you?"

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

        # A time read back with "shall I book that?". Said so the reader can
        # tell "yes, the left one" from a correction of the side.
        if conversation.stage == "confirming":
            return "confirm"

        # A question the agent asked out loud a moment ago, waiting for an
        # answer that only means something against it.
        if conversation.candidates:
            return "exam_choice"

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

    def _hand_over(self, conversation: Conversation, why: str, *, note: str) -> None:
        """Gives the call to a person, and gives back any time it was keeping.

        Every handover comes through here. A time held when somebody asked for
        a person stayed held for nobody until the hold ran out: ten minutes in
        which that slot could not be offered to anybody else, after a call that
        had already ended.
        """
        if conversation.hold:
            self._diary.release(conversation.hold)
            conversation.hold = None
        conversation.hand_over(why, note=note)  # type: ignore[arg-type]

    def _handover(self, state: Turn) -> Turn:
        conversation = state["conversation"]
        reading = state["reading"]

        # Why, in the words somebody counting these at the end of a week can
        # act on. Somebody ringing about an appointment they already have was
        # understood perfectly; calling that "not understood" would put a
        # working part of the agent on the list of things to fix.
        why: str = "not_understood"
        if reading.intent == "operator":
            why = "asked_for_a_person"
        elif reading.intent == "manage":
            why = "already_booked"
        elif reading.intent == "urgent":
            why = "may_be_an_emergency"
        elif reading.intent == "clinical":
            why = "needs_a_clinician"

        note = conversation.summary()
        if reading.concern:
            # In the caller's own words, because "they mentioned a pacemaker"
            # is what the person picking up needs to read before hello.
            note = f"{note}; they said: {reading.concern}"
        self._hand_over(conversation, why, note=note)

        if why == "may_be_an_emergency":
            # Said first and said plainly. Everything after it is less
            # important than the caller hearing it.
            return {
                **state,
                "reply": (
                    "That does not sound like something to wait for an appointment for. "
                    "If this is an emergency, please hang up and call the emergency number now. "
                    "I am putting you through to a colleague."
                ),
            }

        if why == "needs_a_clinician":
            return {
                **state,
                "reply": (
                    "That is something a clinician needs to look at before anything is booked, "
                    "so I am putting you through to a colleague and passing on what you said."
                ),
            }

        if why == "already_booked":
            return {
                **state,
                "reply": (
                    "For an appointment you already have I need to know it is really you, "
                    "and I cannot check that. I am putting you through to a colleague."
                ),
            }

        return {
            **state,
            "reply": (
                "I am putting you through to a colleague — "
                f"I have noted: {conversation.summary()}."
            ),
        }

    def _answer(self, state: Turn) -> Turn:
        named = self._priced(state["reading"].exam_text)
        if named:
            # What it costs, for the exams the question names: all of them
            # when the words fit more than one equally, because quoting one
            # knee's price to somebody asking about the other is a guess.
            prices = " and ".join(f"{exam.name} costs {self._money(exam.price)}" for exam in named)
            return {**state, "reply": f"{prices}. Would you like to book something?"}

        return {
            **state,
            "reply": (
                f"{self._clinic} is open {self._hours}, at {self._address}. "
                "Would you like to book something?"
            ),
        }

    def _priced(self, exam_text: str) -> list:
        """The exams a price question is about: the best matches, up to three."""
        if not exam_text:
            return []
        found = self._catalogue.search(exam_text)
        if not found:
            return []
        best = found[0].score
        return [match.exam for match in found if abs(match.score - best) < 1e-9][:3]

    def _money(self, amount: float) -> str:
        """A price as it is said: whole amounts without the pennies."""
        figure = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
        return f"{self._currency}{figure}"

    def _clarify(self, state: Turn) -> Turn:
        """Asks for exactly one missing thing, and says why it is being asked."""
        conversation = state["conversation"]
        reading = state["reading"]

        if reading.intent == "unclear":
            # The question that was on the table, asked again, and nothing put
            # away. It used to be "what are you looking to book?" whatever had
            # been asked, and it moved the call back to gathering: a caller who
            # mumbled while three times were being read out was then asked
            # what they wanted to book, and their "1" a moment later meant
            # nothing, because the times had been forgotten.
            conversation.stalled_turns += 1
            if conversation.stage == "greeting":
                conversation.stage = "gathering"
            return {**state, "reply": "Sorry — I did not catch that. " + self._question(conversation)}

        conversation.stage = "gathering"
        conversation.stalled_turns = 0

        # Answering the question by position: "the second one", out of the two
        # the agent just read out.
        if reading.intent == "choose" and conversation.candidates:
            picked = chosen_exam(conversation, reading.which or 0)
            if picked is None:
                names = " or ".join(exam.name for exam in conversation.candidates)
                return {**state, "reply": f"Sorry — was that {names}?"}
            conversation.requests = [self._with_what_was_said(conversation, Request(exam=picked))]
            conversation.candidates = []

        self._absorb(conversation, reading)

        # An exam this agent may not book. Said plainly, with the reason, and
        # handed on — not hidden so the clinic looks like it does not do it.
        blocked = conversation.unbookable()
        if blocked:
            exam = blocked[0].exam
            self._hand_over(conversation, "cannot_be_booked_here", note=exam.name)
            return {
                **state,
                "reply": (
                    f"{exam.name} cannot be booked here: {exam.unbookable_reason}. "
                    "I am passing you to a colleague who can arrange it."
                ),
            }

        missing = conversation.missing()

        if missing == ("exam",):
            found = self._where_to_look(conversation).search(reading.exam_text or state["said"])

            # Only the ones that scored as well as the best. Everything the
            # search returned was offered before, so a message that named an
            # MRI clearly was answered with "did you mean an MRI, an
            # ultrasound, or an x-ray?" — three names, two of them there only
            # because a single word had brushed against them.
            if found:
                best = found[0].score
                close = [match.exam for match in found if abs(match.score - best) < 1e-9]
                if len(close) > 1:
                    conversation.candidates = close[:3]
                    names = " or ".join(exam.name for exam in conversation.candidates)
                    return {**state, "reply": f"Did you mean {names}?"}

            conversation.candidates = []
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

        # A new set of times replaces a time already held: a side corrected
        # while one was being read back would otherwise leave that one held
        # for nobody until it ran out.
        if conversation.hold:
            self._diary.release(conversation.hold)
            conversation.hold = None

        minutes = conversation.minutes_needed()
        modality = conversation.requests[0].exam.modality

        # Times already turned down, so "no, something else" means something
        # else. Compared on the room and the minute rather than on the object:
        # the diary hands out a fresh Slot every time it is asked.
        already = {(slot.room, slot.starts) for slot in conversation.declined}

        found: list[Slot] = []
        day = self._now.date()
        for _ in range(14):
            for slot in self._diary.free(
                modality=modality, minutes=minutes, day=day, now=self._now
            ):
                if (slot.room, slot.starts) in already:
                    continue
                found.append(slot)
                if len(found) >= OFFERS:
                    break
            if len(found) >= OFFERS:
                break
            day = day + timedelta(days=1)

        if not found:
            # Nothing in a fortnight is not something to keep looking for out
            # loud. A person can offer a waiting list; this cannot.
            self._hand_over(conversation, "booking_failed", note="nothing free in the next two weeks")
            nothing = "anything else" if conversation.declined else "anything"
            return {
                **state,
                "reply": (
                    f"I cannot find {nothing} in the next two weeks. "
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
        conversation.chosen = slot
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

        if not conversation.patient:
            # It used to book "not given" here. The diary refuses that now, as
            # it refuses anything that is not a name, and asking is the answer.
            return {**state, "reply": "And what name should I put it under?"}

        try:
            booking = self._diary.confirm(
                conversation.hold,
                requests=conversation.requests,
                patient=conversation.patient,
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

        # What they turned down, so the next answer is a different one. Reading
        # the same three times back was the whole of this agent's reply to "no,
        # something else" — which is the answer that makes a caller ask for a
        # person, and it passed a test that asserted nothing had changed.
        conversation.declined.extend(conversation.offered)
        conversation.offered = []
        conversation.refused_sets += 1

        if conversation.refused_sets >= SETS:
            self._hand_over(
                conversation,
                "booking_failed",
                note=f"{conversation.summary()}; turned down {SETS} sets of times",
            )
            return {
                **state,
                "reply": (
                    f"None of those suit, and that is {SETS} sets of times. "
                    "I am passing you to a colleague who can look through the whole diary with you."
                ),
            }

        conversation.stage = "gathering"
        return self._offer(state)

    def _question(self, conversation: Conversation) -> str:
        """The question waiting for an answer, in the words it was first asked in."""
        if conversation.stage == "offering" and conversation.offered:
            return "I have these:\n" + _times(conversation.offered) + "\nWhich suits you?"

        if conversation.stage == "confirming" and conversation.chosen is not None:
            when = conversation.chosen.starts
            return (
                f"Shall I book {conversation.summary()}, "
                f"on {when.strftime('%A %d %B')} at {when.strftime('%H:%M')}?"
            )

        if conversation.candidates:
            names = " or ".join(exam.name for exam in conversation.candidates)
            return f"Did you mean {names}?"

        missing = conversation.missing()
        if "side" in missing:
            return "Left or right?"
        if "contrast" in missing:
            return "With or without contrast?"
        if "patient" in missing:
            return "What name should I put it under?"
        return "What are you looking to book?"

    # --------------------------------------------------------------- routing

    def _where_to_look(self, conversation: Conversation) -> Catalogue:
        """The catalogue to read the next message against.

        Narrowed to what the agent just asked about, when it asked. "The MRI
        one" names two exams in a clinic that does two kinds of MRI and exactly
        one when the question was "did you mean the MRI or the x-ray?" — the
        answer is only ambiguous if you have forgotten your own question.
        """
        if conversation.candidates:
            return Catalogue(conversation.candidates)
        return self._catalogue

    @staticmethod
    def _with_what_was_said(conversation: Conversation, request: Request) -> Request:
        """A request, with the side and contrast said before it was clear which exam."""
        made = Request(
            exam=request.exam,
            side=request.side or conversation.said_side,
            contrast=request.contrast if request.contrast is not None else conversation.said_contrast,
        )
        conversation.said_side = None
        conversation.said_contrast = None
        return made

    def _absorb(self, conversation: Conversation, reading: Reading) -> None:
        """Puts what was understood into what is known.

        A name is taken only if the diary would take it. The rules reader
        already asks the same question; a reader that does not would otherwise
        be found out by the diary refusing it at the very end of the call.
        """
        if reading.name and plausible_name(reading.name):
            conversation.patient = reading.name

        if reading.exam_text:
            # Against the question that was asked first, and against the whole
            # catalogue after — somebody is allowed to answer "did you mean the
            # MRI or the x-ray?" with "actually, make it an ultrasound".
            request = self._where_to_look(conversation).resolve(reading.exam_text)
            if request is None and conversation.candidates:
                request = self._catalogue.resolve(reading.exam_text)

            if request is not None:
                request = self._with_what_was_said(conversation, request)

                # The same exam named again keeps what is already known about
                # it. A turn can be read into the state twice, by the routing
                # and then by the node it routes to, and the second reading of
                # "the mri one" replaced a request that had just been given its
                # side and contrast with one that had neither. An answer that
                # repeats the exam is not a request to forget its details.
                if conversation.requests and conversation.requests[0].exam == request.exam:
                    known = conversation.requests[0]
                    request = Request(
                        exam=request.exam,
                        side=request.side or known.side,
                        contrast=request.contrast if request.contrast is not None else known.contrast,
                    )

                conversation.requests = [request]
                conversation.candidates = []
                return

        # Said with an exam that could be either of two: kept until it is
        # clear which, rather than dropped and asked for again a moment later.
        # "Knee, left, no contrast" was answered "MRI or x-ray?", then "left
        # or right?", then "with or without contrast?".
        if not conversation.requests and (reading.side or reading.contrast is not None):
            conversation.said_side = reading.side or conversation.said_side
            if reading.contrast is not None:
                conversation.said_contrast = reading.contrast
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

        # Before everything, as the reader reads them before everything: what
        # cannot wait and what a clinician has to see are not a stage of the
        # booking, they are the end of it.
        if reading.intent in ("urgent", "clinical"):
            return "handover"

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
            # "The second one" answers two different questions — which exam,
            # and which time — and which of them it is depends on what was
            # asked, not on the words.
            return "clarify" if conversation.candidates else "hold"

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

    @staticmethod
    def _noted(name: str, node):
        """A node that writes its own name on the conversation, then runs.

        Wrapped here rather than written into every node, so a node added
        tomorrow is in the record without anybody remembering to put it there.
        """

        def run(state: Turn) -> Turn:
            state["conversation"].moves.append(name)
            return node(state)

        return run

    def _build(self) -> Any:
        graph: StateGraph = StateGraph(Turn)

        graph.add_node("understand", self._understand)
        for name, node in (
            ("clarify", self._clarify),
            ("offer", self._offer),
            ("hold", self._hold),
            ("book", self._book),
            ("another", self._another),
            ("answer", self._answer),
            ("handover", self._handover),
        ):
            graph.add_node(name, self._noted(name, node))

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
