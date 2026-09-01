#!/usr/bin/env python
"""Talk to the agent, without a server and without an account anywhere.

    python -m tools.talk              type at it
    python -m tools.talk --voice      the same call, worded for a telephone
    python -m tools.talk --clinic data/clinic.json

This is the first thing to run in this repository. Everything else — the tests,
the transcripts, the HTTP service — is a way of checking what happens here, and
none of it is worth much if this is not the first minute somebody spends.

It ends when the conversation does, which is the point: a booking, or a person
taking over, and both of them say why.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from booking_agent.channels import for_name
from booking_agent.clinic.build import default, from_file
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Rules
from booking_agent.conversation.state import new


def main(argv: list[str]) -> int:
    parsed = argparse.ArgumentParser(prog="tools.talk", description="Talk to the booking agent.")
    parsed.add_argument("--voice", action="store_true", help="word the replies for a telephone")
    parsed.add_argument("--clinic", help="a clinic file; the one in data/ by default")
    options = parsed.parse_args(argv)

    clinic = from_file(options.clinic) if options.clinic else default()
    channel = for_name("voice" if options.voice else "chat")

    agent = Agent(
        catalogue=clinic.catalogue,
        diary=clinic.diary,
        reader=Rules(clinic.catalogue),
        clinic_name=clinic.name,
        opening_hours=clinic.opening_hours,
        address=clinic.address,
    )
    conversation = new("talk")

    print(f"{clinic.name} — {len(clinic.catalogue)} exams. Ctrl-C to hang up.\n")
    print(f"  agent : {channel.say(agent.greeting())}\n")

    while not conversation.over:
        try:
            said = input("  you   : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  (hung up)")
            return 0

        if not said:
            continue

        # The clock is read here and nowhere below. Passing it in is what lets
        # a test watch a hold run out without waiting ten minutes for it.
        reply = agent.reply_to(conversation, said, now=datetime.now())
        print(f"\n  agent : {channel.say(reply)}\n")

    if conversation.booking is not None:
        print(f"  -- booked, reference {conversation.booking.reference}")
        return 0

    print(f"  -- handed to a person: {conversation.handed_over}")
    if conversation.handover_note:
        print(f"     they will be told: {conversation.handover_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
