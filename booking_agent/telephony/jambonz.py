"""A switchboard's messages in, its verbs out.

jambonz is a programmable switchboard. It answers a number over SIP, does the
listening and the speaking itself, and asks an application what to do next — so
the application never touches audio. It offers two ways of being asked: HTTP
webhooks, and one WebSocket per call. This is the WebSocket side, because its
envelope is the part the published documentation pins down exactly, and a shape
that cannot be checked is not one to write code against.

── What arrives, and what goes back ─────────────────────────────────────────

Every message from the switchboard carries a ``type``, a ``msgid`` and a
``data`` payload. Three types matter here:

  ``session:new``   a call has arrived. It must be answered, and quickly: the
                    documentation gives an application five seconds before the
                    call is dropped, which is why nothing in the path below
                    waits for anything slower than the agent.
  ``verb:hook``     something a verb was told to report back. For a ``gather``
                    that is what the caller said, or that they said nothing.
  ``call:status``   where the call has got to. ``completed`` is the caller
                    hanging up, and it is the only news this file acts on.

The answer to the first two is an ``ack`` quoting the ``msgid`` it answers, and
carrying the verbs to run next. ``call:status`` is a notification and is not
acknowledged.

Three verbs are used, and only three: ``say``, ``gather`` and ``hangup``. A
booking call is a question, an answer, and eventually an ending.

── The two rules that are this file's own ───────────────────────────────────

**Silence is not a sentence.** A ``gather`` that times out, or a transcript the
switchboard itself is not confident in, is not fed to the agent — a guess sent
into a booking is exactly the failure the rest of this project is arranged to
avoid. What happens instead is that the last question is asked again, once, in
the agent's own words. Nothing in this file invents a sentence to say to
anybody: the agent owns the words, and a channel that started writing its own
would be the beginning of the wording living in two places.

**A call is always spoken to as a call.** The channel is ``voice``, so what
comes back is already the reply as somebody who cannot look back needs to hear
it — a numbered list turned into sentences, a time said with the part of the
day attached, a reference spelled out and repeated. The ``say`` verb gets that
text unchanged. This file has no opinion about wording at all, which is the
reason it fits in a page.

── What is deliberately not here ────────────────────────────────────────────

Message types and payload fields whose shape could not be checked against the
published documentation are not handled and not invented: an adapter that
pretends to an interface which does not exist is worse than one that stops
short, and the README says plainly where it stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from booking_agent.telephony.desk import Desk, Gone

#: How this service is asked to say things, and the point of the exercise: the
#: reply arrives already said for somebody who is listening rather than reading.
CHANNEL = "voice"

#: Call statuses that mean there is nothing left to talk to. The switchboard
#: publishes more than these; the rest are a call still on its way somewhere.
ENDED = ("completed", "failed", "no-answer", "busy")

#: The one reason on a hook that carries something the caller actually said.
#: Every other reason — a timeout, a transcript the switchboard is not sure
#: of — is treated as silence, which is the safe direction to be wrong in.
SPOKE = "speechDetected"


def _say(text: str) -> dict[str, Any]:
    return {"verb": "say", "text": text}


def _hangup() -> dict[str, Any]:
    return {"verb": "hangup"}


def _transcript(data: Mapping[str, Any]) -> str:
    """What the caller said, if the switchboard is telling us they said it.

    Two shapes appear in the published examples of a recognised transcript: the
    alternatives directly under ``speech``, and a list of ``transcripts`` each
    with alternatives of its own. Both are read rather than one being picked,
    because picking wrongly is a caller repeating themselves at a machine that
    heard them perfectly well. Anything that is neither is silence, and silence
    has an answer of its own.
    """
    if data.get("reason") != SPOKE:
        return ""

    speech = data.get("speech")
    if not isinstance(speech, Mapping):
        return ""

    # An interim result, if one ever arrives here. `gather` reports finals to
    # its action hook and interim ones elsewhere, so this should not happen —
    # and half a sentence booked as a whole one is worth the two lines.
    if speech.get("is_final") is False:
        return ""

    alternatives = speech.get("alternatives")

    if not alternatives:
        first = (speech.get("transcripts") or [{}])[0]
        alternatives = first.get("alternatives") if isinstance(first, Mapping) else None

    if not alternatives:
        return ""

    best = alternatives[0]
    said = best.get("transcript", "") if isinstance(best, Mapping) else ""
    return said.strip() if isinstance(said, str) else ""


@dataclass
class OnTheLine:
    """One call in progress, and the little this file has to remember of it."""

    #: The reference the service gave this call. Everything said goes to it.
    call: str

    #: The last thing the agent said, kept so that a silence can be answered by
    #: asking it again rather than by this file making something up.
    asked: str

    silences: int = 0


@dataclass
class Jambonz:
    """A switchboard, translated into the three things a caller can do."""

    desk: Desk

    #: The path the switchboard is asked to report a ``gather`` back on. It
    #: comes back on the hook of every message it caused, and there is one, so
    #: nothing here has to route on it.
    hook: str = "/said"

    #: How long a ``gather`` waits for somebody to start speaking. Long enough
    #: to find a prescription on the table, short enough that a call which has
    #: been abandoned does not sit there.
    listens_for: int = 8

    #: How many silences in a row are answered by asking again before the call
    #: is ended. One: somebody who has not answered twice has put the handset
    #: down on the table, and a machine asking a third time is a machine
    #: nobody is listening to.
    patience: int = 1

    _lines: dict[str, OnTheLine] = field(default_factory=dict, repr=False)

    # ---------------------------------------------------------------- in

    def heard(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """One message from the switchboard, and the answer to it if there is one.

        ``None`` means there is nothing to send back, which is a real answer
        and not a failure: a status notification is not acknowledged, and a
        message of a kind this does not handle is left alone rather than
        replied to with a guess.
        """
        kind = message.get("type")

        if kind == "session:new":
            return self._arrived(message)
        if kind == "verb:hook":
            return self._reported(message)
        if kind == "call:status":
            return self._where_it_got_to(message)

        return None

    # ------------------------------------------------------------ the call

    def _arrived(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """A call has come in. Greet it, and start listening in the same breath.

        One verb rather than a ``say`` and then a ``gather``: the greeting is
        the question, and a caller who starts answering before it has finished
        should be heard rather than talked over.
        """
        sid = self._sid(message)
        if sid is None:
            return None

        turn = self.desk.start(channel=CHANNEL)
        self._lines[sid] = OnTheLine(call=turn["call"], asked=turn["reply"])

        return self._ack(message, [self._gather(turn["reply"])])

    def _reported(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """A ``gather`` has something to report: a sentence, or a silence."""
        sid = self._sid(message)
        line = self._lines.get(sid) if sid else None

        if line is None:
            # A hook for a call this never answered. There is no reference to
            # say anything on, and inventing a new call under a switchboard's
            # idea of an old one is how two conversations end up sharing a
            # handset.
            return self._ack(message, [_hangup()])

        said = _transcript(message.get("data") or {})

        if not said:
            return self._ack(message, self._silence(sid, line))

        try:
            turn = self.desk.said(line.call, said)
        except Gone:
            self._forget(sid)
            return self._ack(message, [_hangup()])

        line.silences = 0
        line.asked = turn["reply"]

        if turn.get("over"):
            # Said and then ended, in that order and in one answer: a booking
            # reference the caller does not hear is a booking they have not got.
            self._forget(sid, putting_it_down=True)
            return self._ack(message, [_say(turn["reply"]), _hangup()])

        return self._ack(message, [self._gather(turn["reply"])])

    def _where_it_got_to(self, message: Mapping[str, Any]) -> None:
        """The caller hung up, or never got through. Let the call go.

        Not acknowledged: the documented answer to a status notification is no
        answer at all.
        """
        sid = self._sid(message)
        status = (message.get("data") or {}).get("call_status")

        if sid and status in ENDED:
            self._forget(sid, putting_it_down=True)

        return None

    # ------------------------------------------------------------- silence

    def _silence(self, sid: str, line: OnTheLine) -> list[dict[str, Any]]:
        """Nobody said anything. Ask again, once, and then stop.

        The question asked again is the agent's own last sentence, kept for
        exactly this. The alternative is a line of prose living in a telephony
        adapter, which is where the wording of the thing this replaces had got
        to and the reason it could not be changed.
        """
        line.silences += 1

        if line.silences > self.patience:
            self._forget(sid, putting_it_down=True)
            return [_hangup()]

        return [self._gather(line.asked)]

    # -------------------------------------------------------------- out

    def _gather(self, text: str) -> dict[str, Any]:
        """Say something and listen for the answer to it, as one verb.

        ``bargein`` because a caller who has heard enough of three offered
        times should be able to take the first one without waiting for the
        third — on a telephone, talking over the machine is how people say
        "that one".
        """
        return {
            "verb": "gather",
            "actionHook": self.hook,
            "input": ["speech"],
            "bargein": True,
            "timeout": self.listens_for,
            "say": {"text": text},
        }

    def _ack(self, message: Mapping[str, Any], verbs: list[dict[str, Any]]) -> dict[str, Any]:
        """The answer, quoting the message it answers.

        The ``msgid`` is not decoration: it is how the switchboard knows which
        of the messages it has in flight this belongs to, and a reply that
        loses it is a reply to nothing.
        """
        return {"type": "ack", "msgid": message["msgid"], "data": verbs}

    # ------------------------------------------------------------ keeping

    def _sid(self, message: Mapping[str, Any]) -> str | None:
        """Which call this is about.

        Top level, where every message carries it, falling back to the payload
        of a new session, which carries it in both places.
        """
        sid = message.get("call_sid") or (message.get("data") or {}).get("call_sid")
        return sid if isinstance(sid, str) and sid else None

    def _forget(self, sid: str, *, putting_it_down: bool = False) -> None:
        line = self._lines.pop(sid, None)

        if line is not None and putting_it_down:
            try:
                self.desk.hang_up(line.call)
            except Gone:  # pragma: no cover - the service got there first
                pass

    def __len__(self) -> int:
        """How many calls this is holding. There should be none after the last
        one ends, and a check says so — a switchboard adapter that remembers
        every call it has ever taken grows until it is restarted."""
        return len(self._lines)
