"""The telephone, which is where this agent came from and the one thing the
demonstration could not show.

A caller does not open a console. They dial a number, a switchboard answers,
and the agent is at the other end of it. Everything else in this repository is
about what the agent says; this package is about how a switchboard is told to
say it — and it is here because "it also works on the telephone" is a sentence
anybody can write, and nobody reading a repository can check.

What is checkable is the translation. A switchboard sends messages of a
published shape and expects verbs of a published shape back, so the honest
demonstration is a whole call replayed through the adapter from those messages,
ending in a booking that is really in the diary. That runs in the checks, on any
machine, for ever, without a telephone line, a number or an account.

    desk.py      the three things a caller can do, and who does them
    jambonz.py   a switchboard's messages in, its verbs out

The direction of the dependency is the point: this package is a **client** of
the service, standing exactly where the console stands. Adding a telephone
changed no file that was here before it.
"""

from booking_agent.telephony.desk import Desk, Gone, Service
from booking_agent.telephony.jambonz import Jambonz

__all__ = ["Desk", "Gone", "Jambonz", "Service"]
