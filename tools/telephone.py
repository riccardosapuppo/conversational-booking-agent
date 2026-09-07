#!/usr/bin/env python
"""Replays a telephone call through the adapter and prints both sides of it.

    python -m tools.telephone                     the call in data/telephony
    python -m tools.telephone path/to/call.json   yours
    python -m tools.telephone --json              every message, in full

The checks already replay this call and look at the diary afterwards. This
prints it, which is a different job: what a switchboard sends and what goes
back is the part of the system nobody can see by using it, and a paragraph
claiming the translation is right is worth nothing next to the messages
themselves.

Every message here is the shape a jambonz WebSocket application receives.
Nothing dials anything, nothing reaches the network, and no telephone line,
number or account is involved at any point — the service runs in memory and the
call is a file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from booking_agent.clinic.build import default
from booking_agent.service.api import build
from booking_agent.telephony.desk import Service
from booking_agent.telephony.jambonz import Jambonz

HERE = Path(__file__).resolve().parent.parent
CALL = HERE / "data" / "telephony" / "one-whole-call.json"

#: The same Monday the transcripts use, so the times read out are the same ones
#: whatever day this is run on.
MONDAY = date(2026, 9, 7)
BEFORE = datetime(2026, 9, 4, 9, 0)


def wrapped(text: str, *, under: str, width: int = 74) -> str:
    """One long spoken sentence, folded so it can be read on a terminal."""
    lines: list[str] = []
    line = ""

    for word in text.split():
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()

    lines.append(line)
    return f"\n{' ' * len(under)}".join(lines)


def said_in(message: dict) -> str:
    """What the caller said, or why they did not, for the printed line."""
    data = message.get("data") or {}
    speech = data.get("speech") or {}
    alternatives = speech.get("alternatives") or []

    if alternatives:
        return alternatives[0].get("transcript", "")
    return f"[{data.get('reason') or data.get('call_status') or message.get('type')}]"


def main(argv: list[str]) -> int:
    parsed = argparse.ArgumentParser(
        prog="tools.telephone", description="Replay a telephone call through the adapter."
    )
    parsed.add_argument("call", nargs="?", help="a call file; the one in data/ by default")
    parsed.add_argument("--json", action="store_true", help="print every message in full")
    options = parsed.parse_args(argv)

    path = Path(options.call) if options.call else CALL

    if not path.is_file():
        print(f"There is no call at {path}.")
        return 2

    written = json.loads(path.read_text(encoding="utf-8"))
    clinic = default(starting=MONDAY)
    client = TestClient(build(clinic, clock=lambda: BEFORE))
    switchboard = Jambonz(desk=Service(client))

    print(f"{clinic.name}, on the telephone. Nothing here dials anything.\n")

    for step in written["call"]:
        message = step["message"]
        answer = switchboard.heard(message)

        if options.json:
            print(json.dumps(message, indent=2))
        else:
            print(f"  {message['type']:<14} {said_in(message)}")

        if answer is None:
            if not options.json:
                print("  -> nothing      a status notification is not acknowledged\n")
            continue

        if options.json:
            print("->")
            print(json.dumps(answer, indent=2))
            print()
            continue

        for verb in answer["data"]:
            spoken = verb.get("text") or verb.get("say", {}).get("text", "")
            under = f"  -> {verb['verb']:<11} "
            print(f"{under}{wrapped(spoken, under=under) if spoken else ''}".rstrip())

        print()

    booked = client.get("/bookings").json()["bookings"]

    print("In the diary afterwards:")
    for one in booked:
        print(f"  {one['reference']}  {one['patient']}  {', '.join(one['exams'])}  {one['starts']}  {one['room']}")

    if not booked:
        print("  nothing")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
