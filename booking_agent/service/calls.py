"""The calls in progress, and how long they are kept.

Deliberately in memory, and deliberately not hidden behind an interface that
pretends it might not be. A conversation is worth keeping for as long as it is
happening and no longer: the thing worth writing down is the booking, and the
diary already has that.

What matters here is that they are let go of. A service that keeps every
conversation it has ever had grows until it is restarted, and a conversation
nobody has added to in an hour is one where somebody hung up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from booking_agent.conversation.state import Conversation, new

#: How long after the last thing said a call is forgotten. Long enough that
#: somebody who went to find their prescription can come back, short enough
#: that a busy morning does not accumulate.
KEEP_MINUTES = 60


@dataclass
class Call:
    """One conversation, and how it is being spoken to."""

    conversation: Conversation
    channel: str
    last_heard: datetime


@dataclass
class Calls:
    """Every call in progress."""

    keep: timedelta = timedelta(minutes=KEEP_MINUTES)
    _calls: dict[str, Call] = field(default_factory=dict)

    def start(self, reference: str, *, channel: str, now: datetime) -> Call:
        call = Call(conversation=new(reference), channel=channel, last_heard=now)
        self._calls[reference] = call
        return call

    def get(self, reference: str, *, now: datetime) -> Call | None:
        """The call, if it is still here.

        Expiry is checked on the way in rather than swept on a timer: a service
        with a background thread is a service with a background thread to
        debug, and nothing here is urgent enough to need one.
        """
        call = self._calls.get(reference)
        if call is None:
            return None

        if now - call.last_heard > self.keep:
            del self._calls[reference]
            return None

        return call

    def heard(self, reference: str, *, now: datetime) -> None:
        call = self._calls.get(reference)
        if call is not None:
            call.last_heard = now

    def forget(self, reference: str) -> bool:
        return self._calls.pop(reference, None) is not None

    def tidy(self, *, now: datetime) -> int:
        """Drops everything nobody has spoken to in a while, and says how many.

        Called when a call starts, so a service that is being used cleans up
        after itself and one that is idle does nothing at all.
        """
        gone = [
            reference
            for reference, call in self._calls.items()
            if now - call.last_heard > self.keep
        ]
        for reference in gone:
            del self._calls[reference]
        return len(gone)

    def __len__(self) -> int:
        return len(self._calls)
