#!/usr/bin/env python
"""Runs whole conversations through the agent and says how each one ended.

    python -m tools.transcripts                 the ones in data/conversations
    python -m tools.transcripts path/to/folder  yours

A transcript is a text file, one caller line per line. Blank lines and lines
starting with # are ignored, so a file can explain itself.

This exists because of a mistake made repeatedly on other projects here: every
check written was driven by the same handful of examples the code had been
written for, so everything passed while the first real input failed. The tests
in this repository have the same weakness by construction — they were written
alongside the behaviour they test. This is the way to point the agent at
sentences nobody here has seen.

What it reports is the outcome, because that is what matters. A conversation
ends in one of four ways, and only the last is a fault:

  booked        the caller got an appointment
  handed over   a person took it, and the reason is named
  answered      they asked something, got an answer, and never wanted a booking
  stuck         a booking was underway and never finished, one way or the other

The fourth one existed on its own for a while, and reported a caller who rang
to ask the opening hours as a failure. That is the same mistake this tool was
written to catch, made in the tool: a check that only recognises the case it was
written for.

Nothing is written anywhere and nothing leaves the machine.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from booking_agent.clinic.build import default
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Rules
from booking_agent.conversation.state import new


def lines_of(path: Path) -> list[str]:
    said = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            said.append(line)
    return said


def run(path: Path, *, show: bool) -> str:
    """One transcript through a clinic of its own, so they cannot affect each other."""
    # A Monday, so the diary is open and the same run gives the same answer
    # whatever day it happens to be.
    monday = date(2026, 9, 7)
    clinic = default(starting=monday)
    now = datetime(2026, 9, 4, 9, 0)

    agent = Agent(
        catalogue=clinic.catalogue,
        diary=clinic.diary,
        reader=Rules(clinic.catalogue),
        clinic_name=clinic.name,
        opening_hours=clinic.opening_hours,
        address=clinic.address,
    )

    conversation = new(path.stem)

    for said in lines_of(path):
        reply = agent.reply_to(conversation, said, now=now)
        if show:
            print(f"    caller: {said}")
            print(f"    agent : {reply.splitlines()[0] if reply else ''}")
        if conversation.over:
            break

    if conversation.booking is not None:
        return "booked"
    if conversation.handed_over is not None:
        return f"handed over: {conversation.handed_over}"
    if not conversation.requests and not conversation.offered:
        # They never asked for a booking. Somebody who rings to ask what time
        # the clinic opens and then rings off has had a perfectly good call.
        return "answered"
    return "stuck"


def main(argv: list[str]) -> int:
    show = "--show" in argv
    given = [a for a in argv if not a.startswith("--")]

    here = Path(__file__).resolve().parents[1]
    folder = Path(given[0]) if given else here / "data" / "conversations"

    if not folder.exists():
        print(f"There is nothing at {folder}.", file=sys.stderr)
        return 1

    files = sorted(folder.glob("*.txt"))
    if not files:
        print(f"No transcripts in {folder}. They are .txt files, one caller line per line.")
        return 1

    print(f"Running {len(files)} conversations from {folder}\n")

    stuck = 0
    for path in files:
        outcome = run(path, show=show)
        mark = "STUCK" if outcome == "stuck" else "ok   "
        print(f"  {mark}  {path.stem}: {outcome}")
        if show:
            print()
        if outcome == "stuck":
            stuck += 1

    print()
    if stuck:
        # Going round in circles is the one outcome nobody wants. Being handed
        # to a person is a decision; this is the absence of one.
        print(f"{stuck} of {len(files)} went round in circles. Run again with --show to read them.")
        return 1

    print(f"All {len(files)} ended somewhere: booked, answered, or with a person.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
