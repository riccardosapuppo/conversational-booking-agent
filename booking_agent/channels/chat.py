"""The channel that does almost nothing, on purpose.

The agent already writes for a screen: short lines, a numbered list where there
is a choice to make. So this mostly gets out of the way — and being able to say
that is the point of having it. A channel that has to undo the agent's wording
is a sign the wording belongs to a channel rather than to the agent.

What it does do is tidy the edges, because the agent builds replies by joining
pieces and joins leave whitespace behind.
"""

from __future__ import annotations


class Chat:
    """A reply as it is typed to somebody reading it."""

    name = "chat"

    def say(self, reply: str) -> str:
        lines = [line.rstrip() for line in reply.strip().splitlines()]
        return "\n".join(line for line in lines if line)
