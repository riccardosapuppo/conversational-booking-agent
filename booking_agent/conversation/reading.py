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

from booking_agent.clinic.catalogue import Catalogue, Side, contrast_in, side_in, unsure

Intent = Literal[
    "book",
    "choose",
    "agree",
    "refuse",
    "info",
    "manage",
    "operator",
    "greeting",
    #: Something that should not be waiting for a slot in a diary at all.
    "urgent",
    #: Something a clinician has to weigh before anything is booked.
    "clinical",
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

    #: What was said that made it urgent or clinical, in the caller's words,
    #: for the note the person taking over reads first.
    concern: str = ""

    def __bool__(self) -> bool:
        return self.intent != "unclear"


class Reader(Protocol):
    """Anything that can turn a message into a reading."""

    def read(self, text: str, *, expecting: str = "") -> Reading: ...


# What people say, in the order the rules look for it. Order matters: somebody
# who says "no, I want a person" is asking for a person, not refusing a slot.

#: What cannot wait for a slot in a diary. Looked for before anything else,
#: including a request for a person: both end with a person, and only one of
#: them should be told to put the telephone down and call the emergency number.
#:
#: The first run of the golden calls is why this exists. "I have chest pain
#: right now" was read as a request for a chest x-ray, the right one, and
#: booked for Monday morning.
_URGENT = (
    "chest pain",
    "chest pains",
    "pain in my chest",
    "can t breathe",
    "cant breathe",
    "cannot breathe",
    "can not breathe",
    "struggling to breathe",
    "short of breath",
    "difficulty breathing",
    "trouble breathing",
    "heart attack",
    "stroke",
    "unconscious",
    "collapsed",
    "fainted",
    "bleeding",
    "broken my",
    "broke my",
    "broken arm",
    "broken leg",
    "broken bone",
    "fractured",
    "severe pain",
    "pain is really bad",
    "pain is so bad",
    "really bad pain",
    "emergency",
    "ambulance",
)

#: What a clinician has to weigh before an exam is booked: a pregnancy and
#: radiation, kidneys and contrast, an allergy to contrast, a pacemaker and a
#: magnet, a question about medication.
#:
#: Deliberately crude in one direction. "I'm not pregnant" hands over too, and
#: that is a person spending a minute on a caller who did not need one; reading
#: past "pregnant" because a "not" was near it is the same mistake made the
#: other way round, and that one is an x-ray during a pregnancy.
_CLINICAL = (
    "pregnant",
    "pregnancy",
    "expecting a baby",
    "breastfeeding",
    "kidney failure",
    "kidney disease",
    "kidney problem",
    "kidney problems",
    "kidney transplant",
    "renal",
    "dialysis",
    "allergic",
    "allergy",
    "allergies",
    "reaction to contrast",
    "reaction to the contrast",
    "pacemaker",
    "defibrillator",
    "implant",
    "implants",
    "metal in my",
    "metal plate",
    "medication",
    "medicine",
    "tablets",
    "pills",
    "stop taking",
    "blood thinners",
    "insulin",
)

_OPERATOR = (
    "operator",
    "human",
    "person",
    "someone",
    "somebody",
    "anyone",
    "anybody",
    "receptionist",
    "speak to a",
    "speak to an",
    "talk to a",
    "talk to an",
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

_AGREE = (
    "yes",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "fine",
    "perfect",
    "correct",
    "that one",
    "go ahead",
    "that s right",
    "thats right",
)
_REFUSE = ("no", "nope", "not that", "another", "something else", "different", "later")

#: Agreeing with a no in it. Read before the refusals, which would otherwise
#: take the no and turn "no problem" into a request for other times.
_AGREE_ANYWAY = ("no problem", "no worries", "why not", "of course")

#: What takes back something said a moment ago: "sorry, I meant without
#: contrast", "no wait, no contrast", "make it the right one". A side or a
#: contrast said with one of these is a correction, whatever was asked.
_CORRECTING = ("no", "sorry", "actually", "wait", "meant", "mean", "rather", "instead", "make it")

_BOOK = (
    "book",
    "appointment",
    "schedule",
    "when can",
    "availability",
    "slot",
    "need a",
    "need an",
    "want a",
    "want an",
)

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

    A marker with a space in it is a phrase, and is looked for as the same
    words in the same order. It used to be looked for as written, as letters,
    which is the substring mistake one size up: "need a" was found inside
    "need an", and "can't breathe" was not found in "can’t breathe" because
    the apostrophe was the other one. Words in order are what a phrase is.
    """
    return _first(text, markers) != ""


def _first(text: str, markers: tuple[str, ...]) -> str:
    """The first of these the message contains, as words; nothing if none."""
    words = _tokens(text)
    single = set(words)

    for marker in markers:
        wanted = marker.split()
        if len(wanted) == 1:
            if wanted[0] in single:
                return marker
            continue
        for start in range(len(words) - len(wanted) + 1):
            if words[start : start + len(wanted)] == wanted:
                return marker

    return ""


def _yes_or_no(text: str) -> Literal["agree", "refuse"] | None:
    """Whether a message agrees, refuses, or does neither.

    Refusing is looked for before agreeing, because a message with both in it
    is not a yes. "Not that one" contains "that one" and was read as agreeing
    to it, which to "shall I book that?" books the time just turned down; the
    direction it is safe to be wrong in is the one that asks again. A yes with
    a "not" in it ("not ok", "that's not fine") is neither, and nor is one with
    a "no" that is part of agreeing ("no problem"), which is read first.
    """
    if _has(text, _AGREE_ANYWAY):
        return "agree"
    if _has(text, _REFUSE):
        return "refuse"
    if _has(text, _AGREE) and "not" not in _tokens(text):
        return "agree"
    return None


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

        # What cannot wait, before anything else at all: before a name, before
        # an exam, and before a request for a person, because both end with a
        # person and only one of them should be told to call the emergency
        # number first.
        urgent = _first(lowered, _URGENT)
        if urgent:
            return Reading(intent="urgent", concern=urgent)

        # Then what a clinician has to see. Before the exam is read, because
        # "an MRI, I have a pacemaker" is not an MRI booking with a remark
        # attached to it; it is a question only a clinician can answer.
        clinical = _first(lowered, _CLINICAL)
        if clinical:
            return Reading(intent="clinical", concern=clinical)

        # Asked for a person, before anything else. Somebody saying "no, give
        # me a human" is not refusing a slot, and reading it as one is how an
        # agent traps the person who most wants out of it.
        if _has(lowered, _OPERATOR):
            return Reading(intent="operator")

        exam_text = self._exam_words(text)

        # Not knowing, said as an answer. "I'm not sure" has the word "sure" in
        # it and was read as a yes, which to "shall I book that?" is a booking
        # nobody agreed to. With an exam in it, it is still worth reading the
        # exam: "not sure, the MRI I think" has said which.
        if unsure(text) and not exam_text:
            return Reading(intent="unclear")

        # An answer to the question just asked.
        #
        # "Left" is a whole reply, and on its own it means nothing at all — it
        # names no exam, agrees to nothing, asks nothing. Without knowing what
        # was asked, the agent heard it as gibberish and asked again, which is
        # the loop that makes people hang up. What is expected is not a hint
        # here; it is most of the meaning.
        # Both are read whichever of the two was asked about, because people
        # answer the question and then keep going: "left, no contrast" is one
        # reply to "left or right?", and taking only the side out of it means
        # asking about the contrast a moment later and hearing the caller
        # repeat themselves. Answering more than you were asked is normal; an
        # agent that only hears its own question is a form being read out.
        if expecting in ("side", "contrast"):
            side = side_in(text)
            wanted = contrast_in(text)
            if side is not None or wanted is not None:
                return Reading(intent="book", side=side, contrast=wanted)

        if expecting == "contrast":
            # "Yes" and "no" are answers to "with or without contrast?" too,
            # and only when that is the question: read here so that "no" is the
            # answer rather than turning something down.
            answer = _yes_or_no(lowered)
            if answer is not None:
                return Reading(intent="book", contrast=answer == "agree")

        # A side or a contrast taking back one said a moment ago, whatever
        # the question on the table was. Asked for a name, "no wait, no
        # contrast" was taken as the name, four words long, and the booking
        # went ahead with the contrast the caller had just turned down. While
        # a name or nothing in particular is being asked, any side or contrast
        # is news; while times are being read out or one is being confirmed, it
        # has to come with something that takes the last answer back, or "yes,
        # the left one" would stop being a yes.
        if not exam_text and (expecting in ("patient", "") or _has(lowered, _CORRECTING)):
            side = side_in(text)
            wanted = contrast_in(text)
            if side is not None or wanted is not None:
                return Reading(intent="book", side=side, contrast=wanted)

        # A name, when a name is what was asked for. Only then: "Mario" in the
        # middle of a booking is not necessarily the patient.
        if expecting == "patient":
            name = text.strip()
            if name and not _has(lowered, _OPERATOR) and len(name.split()) <= 4:
                return Reading(intent="book", name=name)

        if _has(lowered, _MANAGE):
            return Reading(intent="manage")

        # Picking one of the offered times. Only when something was offered:
        # "two" in "two exams" is not a choice of slot.
        if expecting == "slot":
            which = _ordinal_in(lowered)
            if which is not None:
                return Reading(intent="choose", which=which)

        if not exam_text:
            answer = _yes_or_no(lowered)
            if answer is not None:
                return Reading(intent=answer)

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
