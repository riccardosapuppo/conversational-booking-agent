"""Opening the console when the service starts.

A URL printed in a terminal is a URL somebody has to notice, select and paste.
That is a small tax charged at exactly the wrong moment — the first ten seconds,
before whoever is looking has decided whether this is worth their time.

── The four times it must not ────────────────────────────────────────────────

A program that opens a browser when nobody is watching is worse than one that
never does, and each of these has a way of being wrong that is hard to diagnose:

1. ``--no-open``, because somebody said so;
2. ``NO_OPEN`` in the environment, the same thing for a script that cannot pass
   arguments;
3. ``CI`` is set — a runner has no browser, and on some of them the launcher
   blocks rather than failing, turning a green job into one that hangs;
4. nothing is attached to the terminal. A service started by a supervisor or by
   another program has no person in front of it, and opening a window on the
   machine's console is at best a surprise.

It never stops the service starting, either. A browser that will not open is a
nuisance; a service that will not start because of one is a fault.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser


def open_it(url: str, *, no_open: bool = False, environ: dict[str, str] | None = None, is_tty: bool | None = None) -> tuple[bool, str]:
    """Opens ``url``, or says why not.

    :returns: ``(opened, why)`` — the reason always, because silence about not
        opening is how "it did not open" becomes a bug report about the service
        being broken.
    """
    env = os.environ if environ is None else environ
    tty = sys.stdout.isatty() if is_tty is None else is_tty

    if no_open:
        return False, "--no-open was given"

    if env.get("NO_OPEN") not in (None, "", "0"):
        return False, "NO_OPEN is set"

    if env.get("CI") not in (None, "", "false"):
        return False, "this is CI"

    if not tty:
        return False, "nothing is attached to this terminal"

    try:
        # In a thread, and after a moment. `webbrowser.open` can block for a
        # second or two while the platform works out which browser to use, and
        # doing that before `uvicorn.run` means the browser sometimes arrives
        # at a port nothing is listening on yet — which looks exactly like the
        # service being broken.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        return True, "opening in the default browser"
    except Exception as why:  # pragma: no cover - a platform with no browser
        return False, f"could not open a browser: {why}"
