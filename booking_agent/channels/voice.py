"""The same reply, for somebody who is listening rather than reading.

Everything here comes from one fact: a listener cannot look back. They cannot
re-read the second option while you are saying the third, they cannot see that
"09:00" is a time rather than a number, and they are writing the reference down
with a pen while you say it.

So four things change, and nothing else does:

  a numbered list becomes sentences, because "one close paren" is not a word;
  a date becomes the way somebody would say it, not the way it is stored;
  a time becomes words, with the part of the day attached, because "seven"
    alone gets people to a clinic twelve hours early;
  a reference is spelled out, slowly, and offered twice.

This works on the finished reply rather than on the agent's own idea of what it
is saying, which is the compromise in this file. It holds because the formats
it recognises are produced in one place and are covered by a test that puts
every reply the agent gives during the transcripts through here and fails on
anything left unsayable. If a third channel ever appears, that is the point at
which the agent should hand over its pieces instead of its prose.
"""

from __future__ import annotations

import re

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)

_TENS = ("", "", "twenty", "thirty", "forty", "fifty")

_ORDINALS = {
    1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth",
    9: "ninth", 12: "twelfth", 20: "twentieth", 30: "thirtieth",
}

#: How the agent numbers what it offers, and how a reference is written.
_NUMBERED = re.compile(r"^(\d+)\)\s*(.+)$")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_DAY = re.compile(r"\b(\d{2}) (January|February|March|April|May|June|July|August|September|October|November|December)\b")
_REFERENCE = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b")

_POSITIONS = ("The first is", "The second is", "The third is", "The fourth is", "The fifth is")


def _said(number: int) -> str:
    """A number under sixty, in words."""
    if number < 20:
        return _ONES[number]
    tens, ones = divmod(number, 10)
    return _TENS[tens] if not ones else f"{_TENS[tens]}-{_ONES[ones]}"


def _ordinal(number: int) -> str:
    """The seventh, not the 07th."""
    if number in _ORDINALS:
        return _ORDINALS[number]
    if number > 20 and number % 10 in _ORDINALS:
        return f"{_TENS[number // 10]}-{_ORDINALS[number % 10]}"
    return _said(number) + "th"


def _spoken_time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))

    # Morning or afternoon, always. A clinic that says "seven" to somebody
    # writing it down gets one caller in twelve hours early and one in twelve
    # hours late, and neither of them finds out until the day.
    part = "in the morning" if hour < 12 else "in the afternoon" if hour < 18 else "in the evening"
    told = hour if 1 <= hour <= 12 else abs(hour - 12) or 12

    if minute == 0:
        return f"{_said(told)} o'clock {part}"
    if minute < 10:
        return f"{_said(told)} oh {_said(minute)} {part}"
    return f"{_said(told)} {_said(minute)} {part}"


def _spoken_day(match: re.Match[str]) -> str:
    return f"the {_ordinal(int(match.group(1)))} of {match.group(2)}"


def _spelled(match: re.Match[str]) -> str:
    """A reference, one character at a time, with the dash said as a word."""
    written = match.group(1)
    return " ".join("dash" if character == "-" else character for character in written)


class Voice:
    """A reply as it would be said down a telephone."""

    name = "voice"

    def say(self, reply: str) -> str:
        spoken: list[str] = []

        for line in reply.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            numbered = _NUMBERED.match(line)
            if numbered:
                position = int(numbered.group(1))
                lead = _POSITIONS[position - 1] if position <= len(_POSITIONS) else "Then"
                spoken.append(f"{lead} {numbered.group(2).rstrip('.')}.")
            else:
                spoken.append(line)

        said = " ".join(spoken)

        said = _DAY.sub(_spoken_day, said)
        said = _TIME.sub(_spoken_time, said)

        # An em dash is a pause on the page and a stumble out loud.
        said = said.replace(" — ", ", ").replace("—", ", ")

        # Said once, then said again, because there is no scrolling back to a
        # reference somebody has half written down.
        found = _REFERENCE.search(said)
        if found:
            said = _REFERENCE.sub(_spelled, said)
            said += f" That is {_spelled(found)}, once more."

        return said
