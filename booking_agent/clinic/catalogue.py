"""What a clinic offers, and how to find it from what somebody says.

Nobody rings up asking for "RM ginocchio dx senza mdc". They say "una risonanza
al ginocchio destro", or "la risonanza che mi ha prescritto il dottore", or
just "ginocchio". Turning that into one row of a catalogue is the first thing
this agent has to do and the thing it most often has to do twice, because the
answer is frequently a question back.

So resolution here never guesses. It returns everything it found, ranked, and
says which of the parts it is still missing — the side, whether contrast is
involved — because a booking made for the wrong knee is worse than a booking
that took one more turn to make.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

Side = Literal["left", "right", "both"]

#: Words that name a side, in the language people actually use on the phone.
_SIDES: dict[str, Side] = {
    "left": "left",
    "l": "left",
    "right": "right",
    "r": "right",
    "both": "both",
    "bilateral": "both",
}

#: The words that name contrast at all.
_CONTRAST = {"contrast", "enhanced", "gadolinium"}

#: What turns the word before it into a refusal. Read as words rather than as
#: whole phrases: "no-contrast", "no contrast" and "without contrast" are the
#: same request typed three ways, and splitting on the hyphen leaves the word
#: "contrast" standing on its own — which reads as the opposite of what was
#: asked for, in the one place where being wrong is worst.
_REFUSALS = {"no", "not", "without", "non", "none", "never"}

#: Words that refuse contrast without naming it.
_PLAIN = {"plain", "unenhanced", "noncontrast"}

#: Words that name nothing, on either side of the match.
#:
#: Here because of a real failure rather than for tidiness. The synonym "scan of
#: the tummy" put *of* and *the* into an exam's vocabulary, so every message
#: containing the word "the" matched it: a caller who said "it's about the
#: thing" was understood to want an abdominal ultrasound and was asked for their
#: name. Nothing in the catalogue was wrong — the word "the" was simply allowed
#: to count as evidence.
#:
#: Dropped from both sides, so an exam cannot answer to them either. The words
#: that do the real work here — a side, a contrast, an ordinal — are read from
#: the raw message elsewhere and are untouched by this.
_NOISE = {
    "a", "an", "the", "of", "for", "to", "and", "or", "on", "in", "at",
    "my", "me", "i", "it", "its", "is", "am", "be", "do", "did", "you", "your",
    "we", "us", "this", "that", "these", "those", "there", "here",
    "please", "thanks", "hello", "hi",
    "need", "needs", "want", "wants", "would", "like", "get", "got",
    "have", "has", "book", "booking", "appointment",
    "some", "any", "one", "thing", "about", "from", "with", "without",
    "can", "could", "should", "s", "t",
}


def _content(words: Iterable[str]) -> set[str]:
    """The words that carry a meaning worth matching on."""
    return {word for word in words if word not in _NOISE}


@dataclass(frozen=True)
class Exam:
    """One thing the clinic can be booked for."""

    code: str
    name: str
    modality: str
    minutes: int
    price: float

    #: Other names for it. A catalogue that only answers to its own wording is
    #: a catalogue nobody can search.
    synonyms: tuple[str, ...] = ()

    #: Whether the exam is of a body part that has a left and a right. Asking
    #: which side for a chest x-ray is how an agent sounds like a form.
    needs_side: bool = False

    #: Whether contrast is a choice for this exam rather than a property of it.
    needs_contrast: bool = False

    #: Some things a clinic performs cannot be booked by an agent: they need a
    #: doctor to decide, or a preparation to be explained first. They stay in
    #: the catalogue so they can be found and explained, not hidden so they
    #: look like they do not exist.
    bookable: bool = True
    unbookable_reason: str = ""

    def words(self) -> set[str]:
        """Every word this exam answers to, lowered and split.

        Without the ones that name nothing: a synonym is written the way a
        person would say it, so it carries "of" and "the" along with the words
        that mean something, and an exam that answers to "the" answers to
        everything.
        """
        found: set[str] = set()
        for phrase in (self.name, *self.synonyms):
            found.update(_words(phrase))
        return _content(found)


@dataclass(frozen=True)
class Request:
    """What somebody asked for, as far as it has been understood.

    Deliberately not an ``Exam``: the parts arrive over several turns, and a
    half-filled request is the normal state of this conversation rather than an
    error to be avoided.
    """

    exam: Exam
    side: Side | None = None
    contrast: bool | None = None

    def missing(self) -> tuple[str, ...]:
        """What still has to be asked before this can be booked."""
        gaps: list[str] = []
        if self.exam.needs_side and self.side is None:
            gaps.append("side")
        if self.exam.needs_contrast and self.contrast is None:
            gaps.append("contrast")
        return tuple(gaps)

    @property
    def complete(self) -> bool:
        return not self.missing()


@dataclass(frozen=True)
class Match:
    """One candidate, and how sure the catalogue is about it."""

    exam: Exam
    score: float
    matched: frozenset[str] = field(default_factory=frozenset)


def _words(text: str) -> list[str]:
    """The words of a phrase, lowered, with punctuation dropped.

    Written out rather than handed to a regular expression: what people type
    into a chat carries accents, hyphens and apostrophes, and a pattern built
    by hand out of that is either wrong or slow to read.
    """
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


class Catalogue:
    """Every exam a clinic offers, and the search over it."""

    def __init__(self, exams: Iterable[Exam]) -> None:
        self._exams = tuple(exams)
        self._by_code = {exam.code: exam for exam in self._exams}

        if len(self._by_code) != len(self._exams):
            # Two exams sharing a code is a catalogue that cannot be booked
            # from: whichever is found second is unreachable for ever.
            raise ValueError("two exams share a code")

        # An exam whose every word is one of the ones that name nothing can
        # never be found, however it is asked for. Better to hear about it
        # while the file is being read than during a call.
        nameless = [exam.code for exam in self._exams if not exam.words()]
        if nameless:
            raise ValueError(
                "no searchable words in: " + ", ".join(nameless) + " — it could never be found"
            )

    def __len__(self) -> int:
        return len(self._exams)

    def __iter__(self):
        return iter(self._exams)

    def get(self, code: str) -> Exam | None:
        return self._by_code.get(code)

    def search(self, text: str, limit: int = 5) -> list[Match]:
        """Everything that could be what was asked for, best first.

        Scored on how much of what was said each exam accounts for, so "knee"
        finds both knee exams equally and "mri knee" puts the MRI above the
        x-ray. Ties keep catalogue order, so the same question always gets the
        same answer — and a tie at the top is an ambiguity rather than a
        winner, which is what resolve() makes of it.
        """
        asked = _content(_words(text))
        if not asked:
            # Nothing was said that names anything. That is not an empty
            # result to be worked around — it is the answer, and the reader
            # turns it into "I did not catch that" rather than into a guess.
            return []

        found: list[Match] = []
        for exam in self._exams:
            overlap = asked & exam.words()
            if not overlap:
                continue

            # How much of what they said this exam accounts for, and nothing
            # else. Scoring also on how much of the exam's own vocabulary was
            # covered sounds reasonable and is wrong: it divides by the number
            # of synonyms, so the better an exam is described the lower it
            # ranks, and "knee" quietly picked whichever knee had fewer names
            # instead of admitting it could be either.
            found.append(
                Match(exam=exam, score=len(overlap) / len(asked), matched=frozenset(overlap))
            )

        found.sort(key=lambda match: -match.score)
        return found[:limit]

    def resolve(self, text: str) -> Request | None:
        """The one exam this phrase names, with the side and contrast in it.

        ``None`` when nothing matched, and ``None`` when more than one exam
        matched equally well — an agent that picks one of two knees because
        they scored the same is an agent that books the wrong knee.
        """
        matches = self.search(text)
        if not matches:
            return None

        best = matches[0]
        if len(matches) > 1 and abs(matches[1].score - best.score) < 1e-9:
            return None

        return Request(exam=best.exam, side=side_in(text), contrast=contrast_in(text))


def side_in(text: str) -> Side | None:
    """The side named in a phrase, if one is."""
    for word in _words(text):
        if word in _SIDES:
            return _SIDES[word]
    return None


def contrast_in(text: str) -> bool | None:
    """Whether contrast was asked for, refused, or not mentioned.

    Three answers rather than two. Not mentioning contrast is not the same as
    saying no to it, and reading silence as a refusal is how somebody arrives
    for the wrong scan.

    A refusal is read from the word in front, because that is where it is: "no
    contrast", "no-contrast" and "without contrast" are one request typed three
    ways, and all three leave the word "contrast" standing alone once the
    hyphens are gone.
    """
    words = _words(text)

    for position, word in enumerate(words):
        if word in _PLAIN:
            return False
        if word in _CONTRAST:
            before = words[position - 1] if position else ""
            return before not in _REFUSALS

    return None


def load(rows: Sequence[dict]) -> Catalogue:
    """A catalogue from plain data, as it is kept on disc."""
    return Catalogue(
        Exam(
            code=str(row["code"]),
            name=str(row["name"]),
            modality=str(row["modality"]),
            minutes=int(row["minutes"]),
            price=float(row["price"]),
            synonyms=tuple(row.get("synonyms", ())),
            needs_side=bool(row.get("needs_side", False)),
            needs_contrast=bool(row.get("needs_contrast", False)),
            bookable=bool(row.get("bookable", True)),
            unbookable_reason=str(row.get("unbookable_reason", "")),
        )
        for row in rows
    )
