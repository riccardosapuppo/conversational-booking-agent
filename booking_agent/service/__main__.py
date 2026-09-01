"""Runs the service.

    python -m booking_agent.service
    python -m booking_agent.service --port 8080 --clinic data/clinic.json

Bound to localhost and nothing else, with no default that reaches further. A
demonstration that listens on every interface the moment somebody runs it is a
demonstration that has made a decision on their behalf.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from booking_agent.clinic.build import default, from_file
from booking_agent.service.api import build


def main(argv: list[str]) -> int:
    parsed = argparse.ArgumentParser(prog="booking_agent.service", description=__doc__)
    parsed.add_argument("--host", default="127.0.0.1", help="default: localhost only")
    parsed.add_argument("--port", type=int, default=8000)
    parsed.add_argument("--clinic", help="a clinic file; the one in data/ by default")

    options = parsed.parse_args(argv)
    clinic = from_file(options.clinic) if options.clinic else default()

    print(f"{clinic.name}: {len(clinic.catalogue)} exams, open {clinic.opening_hours}")
    print(f"http://{options.host}:{options.port}/docs")

    uvicorn.run(build(clinic), host=options.host, port=options.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
