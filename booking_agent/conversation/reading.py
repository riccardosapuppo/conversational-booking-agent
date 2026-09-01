"""Working out what somebody meant, behind an interface with two sides.

This is the only part of the agent that has an opinion about natural language,
and it is deliberately the only part. Everything else — the graph, the diary,
the catalogue — works on a ``Reading``, which is plain data. So the whole of the
agent's behaviour can be tested without a model, and the model can be changed
without touching a line of the behaviour.

The default reader is rules. That is not a placeholder for a model that will
arrive later; it is what makes this project runnable. A demonstration that needs
an account and a key before it does anything is a demonstration nobody runs, and
an agent whose logic can only be exercised through a model is an agent whose
logic is not tested.

The rule that matters most here: **not understanding is an answer.** A reader
that guesses produces a confident booking for the wrong thing, and the caller
finds out on the day. ``unclear`` is what sends the conversation to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from booking_agent.clinic.catalogue import Catalogue, Side, contrast_in, side_in

Intent = Literal[
    "book",
    "choose",
    "agree",
    "refuse",
    "info",
    "manage",
    "operator",
    "greeting",
    "unclear",
]


@dataclass(frozen=True)
class Reading:
    """What one message appears to mean."""

    intent: Intent

    #: The words that named an exam, if any. Left as text: turning it into a
    #: catalogue row is the catalogue's job, and it is better at it.
    exam_text: str = ""

    side: Side | None = None
    contrast: bool | None = None

    #: Which of the offered slots, counted from one, when they picked one.
    which: int | None = None

    #: A name, when the message is plainly a name and nothing else.
    name: str = ""

    def __bool__(self) -> bool:
        return self.intent != "unclear"


class Reader(Protocol):
    """Anything that can turn a message into a reading."""

    def read(self, text: str, *, expecting: str = "") -> Reading: ...


# What people say, in the order the rules look for it. Order matters: somebody
# who says "no, I want a person" is asking for a person, not refusing a slot.
_OPERATOR = (
    "operator",
    "human",
    "person",
    "someone",
    "somebody",
    "receptionist",
    "speak to a",
    "talk to a",
    "put me through",
)

_MANAGE = ("cancel", "move", "change", "reschedule", "postpone", "put off")

_INFO = (
    "open",
    "opening",
    "hours",
    "where are you",
    "address",
    "how much",
    "cost",
    "price",
    "parking",
    "how do i get",
)

_AGREE = ("yes", "yeah", "yep", "sure", "ok", "okay", "fine", "perfect", "that one", "go ahead")
_REFUSE = ("no", "nope", "not that", "another", "something else", "different", "later")

_BOOK = ("book", "appointment", "schedule", "when can", "availability", "slot", "need a", "want a")

_GREETING = ("hello", "hi", "good morning", "good afternoon", "good evening")

_ORDINALS = {
    "first": 1,
    "1st": 1,
    "one": 1,
    "second": 2,
    "2nd": 2,
    "two": 2,
    "third": 3,
    "3rd": 3,
    "three": 3,
    "fourth": 4,
    "4th": 4,
    "four": 4,
    "fifth": 5,
    "5th": 5,
    "five": 5,
}


def _tokens(text: str) -> list[str]:
    """The words of a message, lowered, with punctuation dropped."""
    out: list[str] = []
    current: list[str] = []

    for character in text.lower():
        if character.isalnum():
            current.append(character)
        elif current:
            out.append("".join(current))
            current = []

    if current:
        out.append("".join(current))
    return out


def _has(text: str, markers: tuple[str, ...]) -> bool:
    """Whether a message contains any of these, as words rather than as letters.

    A single marker has to be a whole word. Looking for it as a substring reads
    "book" as "ok" and turns "I'd like to book an appointment" into a yes, and
    reads "thing" as "hi" so "it's about the thing from last week" becomes a
    greeting. Both of those happened here, and both were caught by tests rather
    than by reading — which is the same mistake made in three other places in
    this portfolio, always by matching a short word inside a longer one.

    A marker with a space in it is a phrase and is looked for as written.
    """
    words = set(_tokens(text))

    for marker in markers:
        if " " in marker:
            if marker in text:
                return True
        elif marker in words:
            return True

    return False


def _ordinal_in(text: str) -> int | None:
    """Which one they picked, said as a word or a bare number."""
    words = text.replace(",", " ").split()

    for word in words:
        stripped = word.strip(".:;!?")
        if stripped in _ORDINALS:
            return _ORDINALS[stripped]
        if stripped.isdigit():
            number = int(stripped)
            if 1 <= number <= 9:
                return number
    return None


class Rules:
    """The reader that needs nothing installed and nothing signed up for.

    It knows the catalogue, so "knee" is recognised as naming an exam without
    anybody writing the word "knee" into this file. What it does not know it
    says it does not know.
    """

    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def read(self, text: str, *, expecting: str = "") -> Reading:
        lowered = text.strip().lower()

        if not lowered:
            return Reading(intent="unclear")

        # Asked for a person, before anything else. Somebody saying "no, give
        # me a human" is not refusing a slot, and reading it as one is how an
        # agent traps the person who most wants out of it.
        if _has(lowered, _OPERATOR):
            return Reading(intent="operator")

        # An answer to the question just asked.
        #
        # "Left" is a whole reply, and on its own it means nothing at all — it
        # names no exam, agrees to nothing, asks nothing. Without knowing what
        # was asked, the agent heard it as gibberish and asked again, which is
        # the loop that makes people hang up. What is expected is not a hint
        # here; it is most of the meaning.
        if expecting == "side":
            side = side_in(text)
            if side is not None:
                return Reading(intent="book", side=side)

        if expecting == "contrast":
            wanted = contrast_in(text)
            if wanted is not None:
                return Reading(intent="book", contrast=wanted)
            # "Yes" and "no" are answers to "with or without contrast?" too,
            # and checked here so that "no" is read as the answer rather than
            # as turning something down.
            if _has(lowered, _AGREE):
                return Reading(intent="book", contrast=True)
            if _has(lowered, _REFUSE):
                return Reading(intent="book", contrast=False)

        # A name, when a name is what was asked for. Only then: "Mario" in the
        # middle of a booking is not necessarily the patient.
        if expecting == "patient":
            name = text.strip()
            if name and not _has(lowered, _OPERATOR) and len(name.split()) <= 4:
                return Reading(intent="book", name=name)

        if _has(lowered, _MANAGE):
            return Reading(intent="manage")

        exam_text = self._exam_words(text)

        # Picking one of the offered times. Only when something was offered:
        # "two" in "two exams" is not a choice of slot.
        if expecting == "slot":
            which = _ordinal_in(lowered)
            if which is not None:
                return Reading(intent="choose", which=which)

        if _has(lowered, _AGREE) and not exam_text:
            return Reading(intent="agree")
        if _has(lowered, _REFUSE) and not exam_text:
            return Reading(intent="refuse")

        if exam_text:
            return Reading(
                intent="book",
                exam_text=exam_text,
                side=side_in(text),
                contrast=contrast_in(text),
            )

        # Answering "did you mean an MRI or an x-ray?" by position rather than
        # by name. Checked after the exam words and not before them, because
        # "the MRI one" contains the word "one": read as an ordinal it means
        # "the first one", which is a different answer that happens to be right
        # half the time — the worst kind of wrong.
        if expecting == "exam_choice":
            which = _ordinal_in(lowered)
            if which is not None:
                return Reading(intent="choose", which=which)

        # Asked in this order on purpose: somebody saying "how much is an MRI"
        # has named an exam, and answering the price is more use than starting
        # a booking. But "how much" with no exam is still a question.
        if _has(lowered, _INFO):
            return Reading(intent="info")

        if _has(lowered, _BOOK):
            return Reading(intent="book")

        if _has(lowered, _GREETING):
            return Reading(intent="greeting")

        # Nothing recognised. This is a real answer, and the graph turns it
        # into a person rather than into a guess.
        return Reading(intent="unclear")

    def _exam_words(self, text: str) -> str:
        """The message, if the catalogue recognises anything in it.

        The whole message rather than the words that matched: the catalogue
        needs the side and the contrast too, and they are not in its
        vocabulary.
        """
        return text if self._catalogue.search(text) else ""
