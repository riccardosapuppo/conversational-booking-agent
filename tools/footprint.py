#!/usr/bin/env python
"""What installing this costs: how many distributions, and how much on disk.

    python -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt     (.venv\\Scripts\\python on Windows)
    .venv/bin/python -m tools.footprint

Run in a fresh environment with only requirements.txt installed in it, so the
figures are this project's and nothing else's. It counts what is installed in
the interpreter it runs under, leaving pip out because pip comes with every
environment, and adds up the size of the files in its site-packages folders:
the files themselves, not the blocks a disk rounds them up to, so the figure is
the same on any file system.

The README quotes what this printed, with the date and the machine, because a
number copied into prose is right on the day it is copied.
"""

from __future__ import annotations

import os
import platform
import site
import sys
from importlib import metadata
from pathlib import Path

#: Every environment has these, whatever is installed in it.
_EVERYWHERE = {"pip", "setuptools", "wheel"}


def _size(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def measure() -> dict:
    distributions = sorted(
        {
            (one.metadata["Name"] or "").lower()
            for one in metadata.distributions()
            if (one.metadata["Name"] or "").lower() not in _EVERYWHERE
        }
        - {""}
    )

    folders = [Path(folder) for folder in site.getsitepackages() if Path(folder).name == "site-packages"]
    everything = sum(_size(folder) for folder in folders)

    left_out = 0
    for folder in folders:
        for entry in folder.iterdir():
            name = entry.name.lower()
            if any(name == one or name.startswith(one + "-") for one in _EVERYWHERE):
                left_out += _size(entry) if entry.is_dir() else entry.stat().st_size

    return {
        "distributions": distributions,
        "bytes": everything - left_out,
        "python": platform.python_version(),
        "system": f"{platform.system()} {platform.release()}",
    }


def main(argv: list[str]) -> int:
    measured = measure()
    print(f"Python {measured['python']} on {measured['system']}, {sys.prefix}")
    print(f"{len(measured['distributions'])} distributions, pip left out")
    print(f"{measured['bytes'] / 1e6:.1f} MB of files in site-packages")
    if "--list" in argv:
        print("\n".join(f"  {name}" for name in measured["distributions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
