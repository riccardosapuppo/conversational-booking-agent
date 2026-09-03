"""The HTTP interface: start a call, say something, hear what comes back.

Three endpoints, because a booking conversation is three things. The shape of
them is the shape of the conversation and not of the code behind it — nothing
here exposes a graph, a node, a state machine or a catalogue, so any of those
can be replaced without a caller of this noticing.

Two decisions worth their own paragraph.

The clock is passed in. Everything below this file takes the time as an
argument, which is what makes a hold expiring mid-sentence a three-line test;
the price is that somewhere the real clock has to be read, and this is the only
place that does. A test hands in its own and controls time completely.

The channel is chosen when the call starts, not when a reply is sent. The same
caller does not switch from a telephone to a keyboard halfway through, and
deciding it per message means every client has to remember to say it every
time — which one of them eventually will not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from booking_agent import __author__, __version__
from booking_agent.channels import for_name
from booking_agent.clinic.build import Clinic, default
from booking_agent.conversation.graph import Agent
from booking_agent.service.looking import attach
from booking_agent.conversation.reading import Reader, Rules
from booking_agent.service.calls import Calls

#: The longest thing a caller can say in one go. Somebody explaining their
#: symptoms at length is a real message; a megabyte is not a sentence.
LONGEST = 2000


class Start(BaseModel):
    channel: str = Field(default="chat", max_length=40)


class Said(BaseModel):
    text: str = Field(min_length=1, max_length=LONGEST)


class Booked(BaseModel):
    reference: str
    patient: str
    exams: list[str]
    starts: datetime


class Reply(BaseModel):
    """One turn, and everything a client needs to decide what to do next."""

    call: str
    reply: str
    stage: str
    over: bool
    handed_over: str | None = None
    handover_note: str | None = None
    booking: Booked | None = None


def build(
    clinic: Clinic | None = None,
    *,
    reader: Reader | None = None,
    clock: Callable[[], datetime] = datetime.now,
) -> FastAPI:
    """The application, with everything it depends on handed to it.

    A function rather than a module-level app: a test builds one with its own
    clinic and its own clock, and two of them cannot interfere with each other.
    """
    where = clinic or default()
    agent = Agent(
        catalogue=where.catalogue,
        diary=where.diary,
        reader=reader or Rules(where.catalogue),
        clinic_name=where.name,
        opening_hours=where.opening_hours,
        address=where.address,
    )
    calls = Calls()

    api = FastAPI(
        title="Booking agent",
        summary=f"Takes booking calls for {where.name}.",
        version=__version__,
        contact={"name": f"Developed by {__author__}"},
    )

    def answer(reference: str, call, reply: str) -> Reply:
        """One reply, said the way this call is being spoken to."""
        conversation = call.conversation
        booking = conversation.booking

        return Reply(
            call=reference,
            reply=for_name(call.channel).say(reply),
            stage=conversation.stage,
            over=conversation.over,
            handed_over=conversation.handed_over,
            handover_note=conversation.handover_note or None,
            booking=(
                Booked(
                    reference=booking.reference,
                    patient=booking.patient,
                    exams=list(booking.exam_codes),
                    starts=booking.slots[0].starts,
                )
                if booking is not None
                else None
            ),
        )

    def find(reference: str, now: datetime):
        call = calls.get(reference, now=now)
        if call is None:
            # One answer for "never existed" and "was an hour ago", because
            # from out here they are the same thing and telling them apart
            # would say which references have been handed out.
            raise HTTPException(status_code=404, detail="no such call")
        return call

    @api.get("/health")
    def health() -> dict:
        return {
            "version": __version__,
            "developed_by": __author__,
            "clinic": where.name,
            "exams": len(where.catalogue),
            "calls": len(calls),
        }

    @api.post("/calls", status_code=201)
    def start(body: Start) -> Reply:
        now = clock()
        calls.tidy(now=now)

        reference = uuid.uuid4().hex
        call = calls.start(reference, channel=body.channel, now=now)
        return answer(reference, call, agent.greeting())

    @api.post("/calls/{reference}/said")
    def said(reference: str, body: Said) -> Reply:
        now = clock()
        call = find(reference, now)

        if call.conversation.over:
            # Answering would start a second conversation inside the first,
            # under a reference somebody has already been given an outcome for.
            raise HTTPException(status_code=409, detail=f"this call is {call.conversation.stage}")

        reply = agent.reply_to(call.conversation, body.text, now=now)
        calls.heard(reference, now=now)
        return answer(reference, call, reply)

    @api.get("/calls/{reference}")
    def state(reference: str) -> Reply:
        now = clock()
        call = find(reference, now)
        # The last thing said, rather than a new one: asking where a call has
        # got to should not move it on.
        last = next(
            (message.text for message in reversed(call.conversation.messages) if message.who == "agent"),
            agent.greeting(),
        )
        return answer(reference, call, last)

    @api.delete("/calls/{reference}", status_code=204)
    def hang_up(reference: str) -> None:
        if not calls.forget(reference):
            raise HTTPException(status_code=404, detail="no such call")

    # The clinic, read-only: the catalogue, the diary and the book.
    #
    # Beside the agent rather than behind it, so what it says on the telephone
    # can be checked against what the diary actually holds. Nothing in there
    # books anything -- a second way to do the one thing this project is about
    # would be the way nobody tested.
    attach(api, where, clock)

    # The console, if it is here.
    #
    # Mounted last, at the root, so every route above wins over a file with the
    # same name -- a static mount at "/" placed first swallows the API.
    #
    # `check_dir=False` because a source checkout may not have been built and a
    # missing folder should not stop the API from starting. The console is a
    # way in, not the service.
    console = Path(__file__).resolve().parent.parent.parent / "web"
    if console.is_dir():
        api.mount("/", StaticFiles(directory=console, html=True), name="console")

    return api
