"""Runs the service, and opens the console.

    python -m booking_agent.service
    python -m booking_agent.service --port 8080 --clinic data/clinic.json
    python -m booking_agent.service --no-open
    python -m booking_agent.service --ollama qwen2.5:3b     reading with a model on this machine

Bound to localhost and nothing else, with no default that reaches further. A
demonstration that listens on every interface the moment somebody runs it is a
demonstration that has made a decision on their behalf.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from booking_agent.clinic.build import Clinic, default, from_file
from booking_agent.conversation.reading import Reader
from booking_agent.service.api import build
from booking_agent.service.opening import open_it


def reader(model: str | None, url: str, clinic: Clinic) -> Reader | None:
    """The reader asked for: none, which is the rules, or a model through Ollama.

    The model and the server are asked about before anything listens, so a
    model that was never pulled is a sentence saying how to pull it, not a
    console that fails on the first thing anybody types.
    """
    if not model:
        return None
    from booking_agent.model.reader import Model, describe, ollama

    describe(model, url)
    return Model(clinic.catalogue, model=model, send=ollama(url))


def main(argv: list[str]) -> int:
    parsed = argparse.ArgumentParser(prog="booking_agent.service", description=__doc__)
    parsed.add_argument("--host", default="127.0.0.1", help="default: localhost only")
    parsed.add_argument("--port", type=int, default=8000)
    parsed.add_argument("--clinic", help="a clinic file; the one in data/ by default")
    parsed.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the console in a browser",
    )
    parsed.add_argument(
        "--ollama",
        nargs="?",
        const="qwen2.5:3b",
        metavar="MODEL",
        help="read with a model through Ollama on this machine, rather than the rules",
    )
    parsed.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="where Ollama is listening")

    options = parsed.parse_args(argv)
    clinic = from_file(options.clinic) if options.clinic else default()
    try:
        reading = reader(options.ollama, options.ollama_url, clinic)
    except RuntimeError as why:
        print(f"cannot read with {options.ollama}: {why}", file=sys.stderr)
        return 1

    where = f"http://{options.host}:{options.port}"

    print(f"{clinic.name}: {len(clinic.catalogue)} exams, open {clinic.opening_hours}")
    print(f"reading with {options.ollama + ' through Ollama at ' + options.ollama_url if reading else 'the rules'}")
    print(f"{where}/       the console")
    print(f"{where}/docs   the API, described")

    # Said either way. A browser that did not open, in silence, is a
    # service somebody decides is broken.
    opened, why = open_it(f"{where}/", no_open=options.no_open)
    print(f"{'opening the console' if opened else 'not opening a browser'}: {why}")

    uvicorn.run(build(clinic, reader=reading), host=options.host, port=options.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
