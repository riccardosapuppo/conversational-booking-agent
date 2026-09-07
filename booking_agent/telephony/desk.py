"""The three things a caller can do, and the one way of doing them.

The console starts a call, says something into it, and hangs up. That is the
whole of its reach into the agent — three requests, over HTTP, with no fourth
and no private entrance. A telephone wants exactly the same three things, so
the right shape for the part that faces a switchboard is not a second way in
but another client, standing where the console stands.

Being literal about that is the claim worth making: nothing under
``booking_agent/`` that existed before a telephone did had to change to add
one. A second entrance would have been the one nobody tested, in the same way
that a screen with its own path to the diary would have been.

``Desk`` is a protocol for the reason ``Reader`` is one: the translation next
door is worth checking without a service underneath it, and a service is worth
having without a switchboard in front of it. There is one implementation, and
it is not a placeholder — it makes the same requests to the same endpoints as
the page, which is what makes "the same calls" a fact rather than a claim.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx


class Gone(Exception):
    """There is no call at that reference any more, or it has finished.

    One exception for both, because from out here they are the same thing:
    nothing more can be said, and what is left to do is put the telephone down.
    The service tells them apart and is right to; a switchboard cannot act on
    the difference.
    """


class Desk(Protocol):
    """Somewhere a booking call can be had."""

    def start(self, *, channel: str) -> dict[str, Any]: ...

    def said(self, call: str, text: str) -> dict[str, Any]: ...

    def hang_up(self, call: str) -> None: ...


class Service:
    """The desk that is this service, reached the way the console reaches it.

    Given a client, rather than a base URL: a check hands in one that goes
    straight into the application in memory, and a running system hands in one
    pointed at wherever the service actually is. Both are the same requests.

    What comes back is the service's own reply, passed on as it arrived. Three
    of its fields are read by anything above here — ``call``, ``reply`` and
    ``over`` — and they are the same three the console reads. Copying the rest
    into a class of this package's own would be a second description of one
    shape, and the copy is always the one that stops agreeing.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def start(self, *, channel: str) -> dict[str, Any]:
        answered = self._client.post("/calls", json={"channel": channel})
        answered.raise_for_status()
        return answered.json()

    def said(self, call: str, text: str) -> dict[str, Any]:
        answered = self._client.post(f"/calls/{quote(call)}/said", json={"text": text})

        # 404 is a call let go of after an hour of silence; 409 is one that has
        # already ended. Anything else is a fault rather than an outcome, and
        # is left to travel — a switchboard quietly hanging up on a broken
        # service is how a broken service stays broken.
        if answered.status_code in (404, 409):
            raise Gone(answered.status_code)

        answered.raise_for_status()
        return answered.json()

    def hang_up(self, call: str) -> None:
        """Lets the call go. A call that has already gone is not an error here.

        Whoever calls this is putting the telephone down, and there is nothing
        it could usefully do with the news that somebody else got there first.
        """
        answered = self._client.delete(f"/calls/{quote(call)}")

        if answered.status_code not in (204, 404):
            answered.raise_for_status()
