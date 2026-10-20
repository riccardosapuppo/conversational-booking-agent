"""The clinic, as tools a model can call: a Model Context Protocol server.

    python -m booking_agent.mcp                       over standard input and output
    python -m booking_agent.mcp --clinic my.json      for a clinic of your own

Everywhere else in this repository the agent does the reading and the clinic
does the booking. Here the reading is done by whatever model is on the other
end, and what this package hands it is the clinic: seven tools, typed, which
look things up, keep a time while the caller decides, book it, give it back,
or fetch a person.

That is a second way into the diary, and the README argues three times that a
second way in is the one nobody tested. So it is tested the way the first one
is, and the rules a booking has to pass are not written here a second time:
what can be booked, what is still missing from a request, which times are on
offer and what can be written down as a name are the clinic's, and the tools
call them. The golden calls' bookings are made again through this server, by a
client of the protocol, and have to come out of the diary the same.

What the tools cannot do is hear the caller. The rules that end a call on
chest pain or a pacemaker are the reader's, and over this protocol the reader
is the model on the other side: the server's instructions ask it to hand over
in the same cases, and nothing here can check that it does.

Nothing under clinic/, conversation/ or channels/ imports this, and a test says
so. It sits beside service/, not on top of it: no HTTP, no web server, and no
port, because a model host starts it as a process and talks to it down a pipe.
"""

from booking_agent.mcp.server import build

__all__ = ["build"]
