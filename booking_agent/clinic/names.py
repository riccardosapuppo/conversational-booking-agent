"""What may be written in the diary as somebody's name.

Here, in the clinic, rather than in the reader, because the diary is the thing
that has to refuse it. The reader uses the same rule to decide whether "yes" was
a name, and anything else that books through the diary meets it there whether
or not it asked first: a rule kept by the caller is a rule one caller forgets.

The first run of the golden calls is why this exists. Asked for a name, one
caller said "yes", another read out the tax code on their card, a third had a
name with digits where the letters should be, and all three were taken for the
name to book under; a call that carried on after "yes" was booked under it.
Nobody arrives at a clinic as "yes".

It is a check of what a name cannot be, not of what one is. Names are too
various for the second, and a rule that refused "Anne-Marie O'Neill" would be
worse than none. What is refused is digits, codes, the words of a sentence
("I'd rather not say"), and the words of the booking itself.
"""

from __future__ import annotations

from booking_agent.clinic.catalogue import contrast_in, side_in

#: How many words a name runs to before it is more likely a sentence.
LONGEST = 4

#: What people say when asked for a name and they do not give one, and the
#: words of the booking itself. Any one of them in the reply refuses it.
_ANSWERS = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "fine", "perfect",
    "correct", "please", "thanks", "thank", "sorry", "hello", "hi", "bye",
    "maybe", "dunno", "idk", "unsure", "later", "wait", "pardon",
    "what", "why", "who", "how", "when", "where", "which",
    "name", "left", "right", "both", "contrast", "scan", "appointment", "booking",
    "mri", "xray", "knee", "chest", "spine", "first", "second", "third",
}

#: What a sentence is made of and a name is not.
#:
#: Kept to words nobody is called. A first version of this list had every
#: short word a sentence uses, and refused An Nguyen (An is a given name), A.
#: Smith and I. Rossi (initials), Ray Smith, Said Ahmed, Can Yilmaz and Do Van
#: Minh: a person turned away for being called the wrong thing. So the words
#: that are also names, or are also initials, are not here; a sentence has
#: enough other words to give it away.
_SENTENCE = {
    "me", "my", "you", "your", "she", "it", "we", "us", "our", "they", "them",
    "is", "am", "are", "was", "were", "be", "been", "does", "did",
    "didn", "doesn", "isn", "wasn", "aren", "not", "never",
    "have", "has", "had", "need", "want", "know", "say", "tell", "telling",
    "think", "mean", "meant", "rather", "would", "could", "should",
    "the", "of", "for", "about", "with", "without", "and", "but",
    "that", "this", "there", "here", "idea",
}

#: Contractions, whole, which a name with an apostrophe in it is never spelled
#: as: "I'm" refuses a reply, the "I" of "I. Rossi" and the "D'" of D'Amico do
#: not.
_CONTRACTIONS = {
    "i'm", "i'd", "i'll", "i've", "it's", "that's", "don't", "can't", "won't",
    "isn't", "didn't", "doesn't", "wasn't", "aren't",
}


class NotAName(ValueError):
    """Raised when something that is not a name is offered to the diary as one."""


def _parts(written: str) -> list[str]:
    return [part for part in written.replace(",", " ").split() if part]


def why_not_a_name(written: str) -> str:
    """Why this cannot go in the diary as a name; empty when it can."""
    name = written.strip()
    if not name:
        return "nothing was given"

    if any(character.isdigit() for character in name):
        # A code, a reference, or letters read as digits off a card.
        return "it has digits in it, and a name has none"

    parts = _parts(name)
    if len(parts) > LONGEST:
        return f"it is more than {LONGEST} words, which is a sentence rather than a name"

    letters = 0
    for part in parts:
        bare = part.strip(".-'’")
        if not bare:
            return "it is punctuation"
        for character in bare:
            if character.isalpha():
                letters += 1
            elif character not in "-'’.":
                return f"{character!r} is not in anybody's name"

        spelled = bare.lower().replace("’", "'")
        if spelled in _CONTRACTIONS:
            return f"{part!r} is the start of a sentence, not somebody's name"

        # "x-ray" and "o.k." are one word each, joined.
        joined = "".join(character for character in spelled if character.isalpha())
        if joined in _ANSWERS:
            return f"{part!r} is an answer, not somebody's name"

        for word in spelled.replace("'", " ").replace("-", " ").replace(".", " ").split():
            if word in _ANSWERS:
                return f"{word!r} is an answer, not somebody's name"
            if word in _SENTENCE:
                return f"{word!r} makes it a sentence, not somebody's name"

        # A side or a contrast, in any of the ways the catalogue reads one:
        # "lt", "bilateral", "plain". Asked for a name, they are an answer to
        # the question before. An initial is not one, though: the R of "Mario
        # R. Rossi" is a letter of somebody's name, and reading it as the right
        # knee is what the reader was stopped doing.
        if len(joined) > 1 and (side_in(part) is not None or contrast_in(part) is not None):
            return f"{part!r} is a side or a contrast, not somebody's name"

    if letters < 2:
        return "it is too short to be a name"
    return ""


def plausible_name(written: str) -> bool:
    """Whether this can go in the diary as somebody's name."""
    return why_not_a_name(written) == ""
