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

#: How many words a name runs to before it is more likely a sentence.
LONGEST = 4

#: Words that are not names, or not the whole of one. Kept to the ones that
#: give a sentence away; words that are also names (Will, May, Grace, Rose)
#: are not here, because refusing a real name is the worse mistake.
_NOT_NAMES = {
    # agreeing, refusing, being polite
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "fine", "perfect",
    "correct", "please", "thanks", "thank", "sorry", "hello", "hi", "bye",
    # what a sentence is made of, without the letters a contraction leaves
    # behind: the d of "I'd" is also the d of D'Amico, and Don is a name
    "i", "me", "my", "you", "your", "he", "she", "it", "we", "they", "us", "them",
    "is", "am", "are", "was", "be", "do", "does", "did",
    "not", "never", "have", "has", "had", "need", "want", "know",
    "say", "said", "tell", "telling", "think", "mean", "meant", "rather", "would",
    "could", "should", "can", "why", "what", "who", "how", "when", "where", "which",
    "the", "a", "an", "of", "for", "to", "about", "with", "without", "and", "or",
    "but", "that", "this", "there", "here", "wait", "idea", "maybe",
    # the booking's own words
    "name", "left", "right", "both", "contrast", "scan", "appointment", "booking",
    "mri", "x", "ray", "xray", "knee", "chest", "spine", "first", "second", "third",
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
        words = [word for word in bare.lower().replace("’", "'").replace("'", " ").replace("-", " ").split() if word]
        refused = [word for word in words if word in _NOT_NAMES]
        if refused:
            return f"{refused[0]!r} is a word, not somebody's name"

    if letters < 2:
        return "it is too short to be a name"
    return ""


def plausible_name(written: str) -> bool:
    """Whether this can go in the diary as somebody's name."""
    return why_not_a_name(written) == ""
