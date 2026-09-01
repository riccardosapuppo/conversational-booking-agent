"""How a reply is said, which is not the same as what it says.

The agent decides what to tell somebody. That decision is the same whether they
are typing or on the telephone, and it lives in the conversation package where
it can be tested without any of this.

What changes is the saying of it. A numbered list is the clearest thing on a
screen and unusable read aloud; a reference somebody can copy is a reference
somebody else has to write down with a pen; "09:00" is four characters and two
words. So a channel is a last step over the finished reply, and it is the only
part of this that knows a telephone exists.

Kept apart for the reason everything here is kept apart: the thing this
replaces had the wording of its answers, the rules about who may book what, and
the telephone's own quirks in one function, so a change to any of the three was
a change to all three.
"""

from booking_agent.channels.chat import Chat
from booking_agent.channels.voice import Voice

__all__ = ["Chat", "Voice", "for_name"]

#: What somebody might call the telephone.
_SPOKEN = ("voice", "phone", "telephone", "call")


def for_name(name: str) -> Chat | Voice:
    """The channel by name, for a service that is told which one it is.

    Anything unrecognised is chat rather than an error. Getting this wrong
    should give somebody an awkwardly worded reply, not no reply at all.
    """
    return Voice() if name.strip().lower() in _SPOKEN else Chat()
