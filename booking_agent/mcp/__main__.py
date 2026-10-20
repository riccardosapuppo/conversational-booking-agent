"""Runs the server over standard input and output, which is how a model host starts one.

    python -m booking_agent.mcp
    python -m booking_agent.mcp --clinic data/clinic.json

Nothing listens on a port. The host starts this as a process and talks to it
down its standard input and output, so standard output belongs to the
protocol: anything written there that is not a message breaks the connection.
What this has to say to a person goes to standard error, which hosts keep in
their logs.
"""

from __future__ import annotations

import argparse
import sys

from booking_agent.clinic.build import default, from_file
from booking_agent.mcp.server import build


def main(argv: list[str]) -> int:
    parsed = argparse.ArgumentParser(prog="booking_agent.mcp", description=__doc__.splitlines()[0])
    parsed.add_argument("--clinic", help="a clinic file; the one in data/ by default")
    options = parsed.parse_args(argv)

    clinic = from_file(options.clinic) if options.clinic else default()
    print(
        f"{clinic.name}: {len(clinic.catalogue)} exams, over MCP on standard input and output",
        file=sys.stderr,
    )

    build(clinic).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
