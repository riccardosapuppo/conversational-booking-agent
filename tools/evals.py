#!/usr/bin/env python
"""Runs the golden calls through the agent and scores each against what should have happened.

    python -m tools.evals            a line per call, the totals, and the two thresholds
    python -m tools.evals --show     and the whole of every call that did not pass
    python -m tools.evals --json     the same results as JSON, for whatever keeps them

    python -m tools.evals --ollama qwen2.5:3b                read by a model, through Ollama
    python -m tools.evals --ollama qwen2.5:3b --record FILE  and every answer kept, to replay
    python -m tools.evals --replay FILE                      those answers again, with no model
    python -m tools.evals --ollama qwen2.5:3b --development  the sentences the prompt was written on

The calls are in evals/golden.json. Each one says what should have happened by
the end of it in the words of the tools: ``confirm_booking`` with an exam, a
side, a contrast, a patient and a time; ``hand_over`` with a reason; or
``clinic_information``. They are written as what a correct agent does, not as
what this one did on the day, so a call that fails is a call the agent fails.

What is checked is the diary and not what the agent says it did. A booking is
read out of the diary afterwards, side and contrast included, and compared with
the case; a booking that existed before the call has to be exactly as it was;
and a slot still held when the call is over is a slot taken from everybody for
nobody. The reply text is only looked at where a case asks for a phrase.

Every call gets one of four verdicts, and they are not of equal weight:

  pass    what should have happened did, with the arguments it should have had
  miss    it did not, and nothing was booked that should not have been: handed
          to a person instead of booked, asked again, or round in circles
  harm    something was booked that should not have been, something was booked
          wrongly, or a booking that already existed was touched
  error   the agent raised. Not a verdict at all, and the run fails on it

A miss costs a caller a minute with a person. Harm is a wrong scan, the wrong
knee, or somebody who arrives to find their appointment moved. So a call that
ends in harm and is not marked critical fails the run whatever the totals are:
the label on the case was wrong, and a threshold that lets it through is a
threshold that is not about anything.

Two thresholds, both in the file next to the cases: the share of critical calls
that pass, and the share of all calls that pass. Below either, this exits 1.
They are the rules' thresholds. A run through a model is a measurement and not
a gate: it prints them for comparison and fails only on an error, and its
replay fails unless every verdict comes out as it was recorded.

Nothing is written anywhere unless --record says where, and nothing leaves the
machine: the model, when there is one, is on it too.
"""

from __future__ import annotations

import hashlib
import json
import sys
import typing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from booking_agent.clinic.build import Clinic, from_file
from booking_agent.clinic.catalogue import Catalogue, Request, Side
from booking_agent.clinic.diary import Booking, Slot
from booking_agent.conversation.graph import Agent
from booking_agent.conversation.reading import Reader, Rules
from booking_agent.conversation.state import Conversation, Escalation, new

HERE = Path(__file__).resolve().parents[1]
GOLDEN = HERE / "evals" / "golden.json"

#: The kinds of call the set has to cover, and the order they are reported in.
CATEGORIES = (
    "complete",
    "piecemeal",
    "change-of-mind",
    "out-of-domain",
    "person",
    "ocr",
    "missing",
    "dangerous",
)

#: What a call can end in, named as the tool that would have been used for it.
TOOLS = ("confirm_booking", "hand_over", "clinic_information")

#: Read off the domain rather than copied, so a reason added to the agent is a
#: reason a case can name, and a reason nobody has is a typo the loader catches.
REASONS = frozenset(typing.get_args(Escalation))
SIDES = frozenset(typing.get_args(Side))

Verdict = Literal["pass", "miss", "harm", "error"]


class BadCase(ValueError):
    """A case that cannot be scored as written.

    Raised while the file is read, never while it is run: a case with a typo in
    its reason would otherwise be a case that can never pass, and it would sit
    in the totals as a failure of the agent.
    """


# ------------------------------------------------------------------ the file


@dataclass(frozen=True)
class Booked:
    """A booking, as a case describes one: expected, or already in the diary."""

    exam: str
    side: str | None
    contrast: bool | None
    patient: str
    starts: datetime | None = None
    between: tuple[datetime, datetime] | None = None
    room: str | None = None


@dataclass(frozen=True)
class Expect:
    tool: str
    booking: Booked | None = None
    reasons: tuple[str, ...] = ()
    says: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    critical: bool
    held_out: bool
    why: str
    said: tuple[str, ...]
    expect: Expect
    given: tuple[Booked, ...] = ()


@dataclass(frozen=True)
class Golden:
    clinic: Path
    laid_out_from: date
    now: datetime
    critical_threshold: float
    overall_threshold: float
    cases: tuple[Case, ...]
    #: The cases' own fingerprint, so a set of results says which cases it is about.
    version: str


def _keys(where: str, given: dict, allowed: set[str], required: set[str]) -> None:
    """No key missing and none extra.

    Extra is the one that matters. A key spelled wrong is otherwise a key that
    is silently not there: "reasn" leaves a case expecting any reason at all,
    and a case that cannot fail looks exactly like a case that passes.
    """
    if not isinstance(given, dict):
        raise BadCase(f"{where}: expected an object")
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise BadCase(f"{where}: unknown {', '.join(unknown)}")
    missing = sorted(required - set(given))
    if missing:
        raise BadCase(f"{where}: missing {', '.join(missing)}")


def _minute(where: str, written: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(written))
    except ValueError as why:
        raise BadCase(f"{where}: {written!r} is not a time") from why


def _booked(where: str, raw: dict, clinic: Clinic, *, expected: bool) -> Booked:
    allowed = {"exam", "side", "contrast", "patient", "starts", "between", "room"}
    _keys(where, raw, allowed | {"tool"} if expected else allowed, {"exam", "side", "contrast", "patient"})

    exam = clinic.catalogue.get(str(raw["exam"]))
    if exam is None:
        raise BadCase(f"{where}: no exam {raw['exam']!r} in the clinic")

    # The side and the contrast are stated even when the exam has neither, as
    # null: a case that leaves them out has not said which it expects.
    side, contrast = raw["side"], raw["contrast"]
    if side is not None and side not in SIDES:
        raise BadCase(f"{where}: {side!r} is not a side")
    if exam.needs_side != (side is not None):
        raise BadCase(f"{where}: {exam.code} {'needs' if exam.needs_side else 'has no'} side")
    if contrast is not None and not isinstance(contrast, bool):
        raise BadCase(f"{where}: contrast is true, false or null")
    if exam.needs_contrast != (contrast is not None):
        raise BadCase(f"{where}: {exam.code} {'needs' if exam.needs_contrast else 'has no'} contrast")

    patient = str(raw["patient"]).strip()
    if not patient:
        raise BadCase(f"{where}: no patient")

    starts = between = None
    if ("starts" in raw) == ("between" in raw):
        raise BadCase(f"{where}: give a time as starts or as between, and only one of them")
    if "starts" in raw:
        starts = _minute(where, raw["starts"])
    else:
        pair = raw["between"]
        if not isinstance(pair, list) or len(pair) != 2:
            raise BadCase(f"{where}: between is [from, to]")
        between = (_minute(where, pair[0]), _minute(where, pair[1]))
        if not between[0] < between[1]:
            raise BadCase(f"{where}: between ends before it starts")

    room = raw.get("room")
    if room is not None and room not in {one.code for one in clinic.diary.rooms}:
        raise BadCase(f"{where}: no room {room!r} in the clinic")
    if not expected and (starts is None or room is None):
        raise BadCase(f"{where}: a booking already in the diary has an exact time and a room")

    return Booked(
        exam=exam.code,
        side=side,
        contrast=contrast,
        patient=patient,
        starts=starts,
        between=between,
        room=room,
    )


def _expect(where: str, raw: dict, clinic: Clinic) -> Expect:
    if not isinstance(raw, dict) or raw.get("tool") not in TOOLS:
        raise BadCase(f"{where}: tool is one of {', '.join(TOOLS)}")

    tool = raw["tool"]
    says = raw.get("says", [])
    if not isinstance(says, list) or not all(isinstance(one, str) and one.strip() for one in says):
        raise BadCase(f"{where}: says is a list of phrases")

    if tool == "confirm_booking":
        rest = {key: value for key, value in raw.items() if key != "says"}
        return Expect(tool=tool, booking=_booked(where, rest, clinic, expected=True), says=tuple(says))

    if tool == "hand_over":
        _keys(where, raw, {"tool", "reason", "says"}, {"tool", "reason"})
        reasons = raw["reason"] if isinstance(raw["reason"], list) else [raw["reason"]]
        if not reasons:
            raise BadCase(f"{where}: no reason")
        for reason in reasons:
            if reason not in REASONS:
                raise BadCase(f"{where}: {reason!r} is not a reason the agent has: {', '.join(sorted(REASONS))}")
        return Expect(tool=tool, reasons=tuple(reasons), says=tuple(says))

    _keys(where, raw, {"tool", "says"}, {"tool"})
    return Expect(tool=tool, says=tuple(says))


def load(path: Path = GOLDEN) -> Golden:
    """The golden set, checked before anything is run against it."""
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    _keys(
        path.name,
        raw,
        {"about", "clinic", "laid_out_from", "now", "thresholds", "cases"},
        {"clinic", "laid_out_from", "now", "thresholds", "cases"},
    )

    clinic_path = (path.parent.parent / raw["clinic"]).resolve()
    laid_out_from = date.fromisoformat(raw["laid_out_from"])
    clinic = from_file(clinic_path, starting=laid_out_from)

    thresholds = raw["thresholds"]
    _keys("thresholds", thresholds, {"critical", "overall"}, {"critical", "overall"})
    for name, value in thresholds.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise BadCase(f"thresholds: {name} is a share between 0 and 1")

    cases: list[Case] = []
    seen: set[str] = set()

    for position, entry in enumerate(raw["cases"], start=1):
        where = f"case {position}"
        _keys(
            where,
            entry,
            {"id", "category", "critical", "held_out", "why", "said", "expect", "given"},
            {"id", "category", "critical", "held_out", "why", "said", "expect"},
        )
        where = str(entry["id"])
        if not where.strip() or where in seen:
            raise BadCase(f"case {position}: id {where!r} is empty or used twice")
        seen.add(where)

        if entry["category"] not in CATEGORIES:
            raise BadCase(f"{where}: {entry['category']!r} is not one of {', '.join(CATEGORIES)}")
        for flag in ("critical", "held_out"):
            if not isinstance(entry[flag], bool):
                raise BadCase(f"{where}: {flag} is true or false")
        if not str(entry["why"]).strip():
            raise BadCase(f"{where}: a case says why it is here")

        said = entry["said"]
        if not isinstance(said, list) or not said or not all(isinstance(line, str) and line.strip() for line in said):
            raise BadCase(f"{where}: said is a list of things the caller says, none of them empty")

        given = entry.get("given", {})
        _keys(f"{where} given", given, {"booked"}, set())
        already = tuple(
            _booked(f"{where} given", one, clinic, expected=False) for one in given.get("booked", [])
        )

        cases.append(
            Case(
                id=where,
                category=entry["category"],
                critical=entry["critical"],
                held_out=entry["held_out"],
                why=str(entry["why"]),
                said=tuple(line.strip() for line in said),
                expect=_expect(where, entry["expect"], clinic),
                given=already,
            )
        )

    if not cases:
        raise BadCase(f"{path.name}: no cases, and a set of none passes every threshold")

    return Golden(
        clinic=clinic_path,
        laid_out_from=laid_out_from,
        now=datetime.fromisoformat(raw["now"]),
        critical_threshold=float(thresholds["critical"]),
        overall_threshold=float(thresholds["overall"]),
        cases=tuple(cases),
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    )


# ------------------------------------------------------------------ one call


@dataclass
class Result:
    case: Case
    verdict: Verdict
    #: What was wrong, in sentences. Empty when it passed.
    why: list[str] = field(default_factory=list)
    #: What happened, named as the tool would be: or "stuck" when nothing did.
    outcome: str = ""
    reason: str | None = None
    booked: list[str] = field(default_factory=list)
    said: list[tuple[str, str]] = field(default_factory=list)
    moves: list[str] = field(default_factory=list)


def describe(booking: Booking) -> str:
    slot = booking.slots[0]
    what = ", ".join(request.description for request in booking.requests)
    return f"{what} for {booking.patient}, {slot.starts:%a %d %b %H:%M} in {slot.room}"


def _wanted(booked: Booked, catalogue: Catalogue) -> str:
    exam = catalogue.get(booked.exam)
    what = Request(exam=exam, side=booked.side, contrast=booked.contrast).description if exam else booked.exam
    if booked.starts is not None:
        when = f"{booked.starts:%a %d %b %H:%M}"
    else:
        assert booked.between is not None
        when = f"between {booked.between[0]:%a %d %b %H:%M} and {booked.between[1]:%H:%M}"
    return f"{what} for {booked.patient}, {when}" + (f" in {booked.room}" if booked.room else "")


def differences(booking: Booking, wanted: Booked) -> list[str]:
    """Everything about a booking that is not what the case expected."""
    found: list[str] = []

    if len(booking.requests) != 1:
        found.append(f"{len(booking.requests)} exams in one booking")
        return found

    request = booking.requests[0]
    if request.exam.code != wanted.exam:
        found.append(f"the exam is {request.exam.code}, not {wanted.exam}")
    if request.side != wanted.side:
        found.append(f"the side is {request.side}, not {wanted.side}")
    if request.contrast != wanted.contrast:
        found.append(f"contrast is {request.contrast}, not {wanted.contrast}")
    if booking.patient != wanted.patient:
        found.append(f"the patient is {booking.patient!r}, not {wanted.patient!r}")

    starts = booking.slots[0].starts
    if wanted.starts is not None and starts != wanted.starts:
        found.append(f"it starts at {starts:%a %d %b %H:%M}, not {wanted.starts:%a %d %b %H:%M}")
    if wanted.between is not None and not wanted.between[0] <= starts < wanted.between[1]:
        found.append(f"it starts at {starts:%a %d %b %H:%M}, outside the time asked for")
    if wanted.room is not None and booking.slots[0].room != wanted.room:
        found.append(f"the room is {booking.slots[0].room}, not {wanted.room}")

    return found


def seed(clinic: Clinic, already: Booked, now: datetime) -> Booking:
    """Puts a booking that exists before the call into the diary."""
    exam = clinic.catalogue.get(already.exam)
    assert exam is not None and already.starts is not None and already.room is not None
    held = clinic.diary.hold([Slot(room=already.room, starts=already.starts, minutes=exam.minutes)], now=now)
    return clinic.diary.confirm(
        held.reference,
        requests=[Request(exam=exam, side=already.side, contrast=already.contrast)],  # type: ignore[arg-type]
        patient=already.patient,
        now=now,
    )


def outcome_of(conversation: Conversation, new_bookings: list[Booking]) -> str:
    """What the call ended in, read off the state rather than off the words."""
    if new_bookings:
        return "confirm_booking"
    if conversation.handed_over is not None:
        return "hand_over"
    # Answered means the agent gave information and nothing was underway: a
    # caller who asks the opening hours and rings off has had a good call.
    if "answer" in conversation.moves and not conversation.requests and not conversation.offered:
        return "clinic_information"
    return "stuck"



def judge(
    case: Case,
    conversation: Conversation,
    clinic: Clinic,
    seeded: list[Booking],
    said: list[tuple[str, str]],
    *,
    now: datetime,
) -> Result:
    """The verdict on one finished call, from the diary and the state it left.

    The words are looked at for one thing only, the phrases a case asks for.
    Everything else is read off the diary and the conversation's state, which
    is what the people at the desk would read too.
    """
    diary = clinic.diary
    before = {booking.reference for booking in seeded}
    new_bookings = [booking for booking in diary.bookings() if booking.reference not in before]
    outcome = outcome_of(conversation, new_bookings)

    result = Result(
        case=case,
        verdict="pass",
        outcome=outcome,
        reason=conversation.handed_over,
        booked=[describe(booking) for booking in new_bookings],
        said=list(said),
        moves=list(conversation.moves),
    )

    harm: list[str] = []
    for original in seeded:
        if diary.booking(original.reference) != original:
            harm.append(f"the booking that was already there ({describe(original)}) was changed or removed")

    wanted = case.expect
    if wanted.tool != "confirm_booking" and new_bookings:
        harm.extend(f"booked {line}, when nothing should have been" for line in result.booked)
    elif wanted.tool == "confirm_booking":
        assert wanted.booking is not None
        if len(new_bookings) > 1:
            harm.append(f"booked {len(new_bookings)} times: {'; '.join(result.booked)}")
        for booking in new_bookings[:1]:
            wrong = differences(booking, wanted.booking)
            if wrong:
                harm.append(f"booked {describe(booking)}: {'; '.join(wrong)}")

    if harm:
        result.verdict = "harm"
        result.why = harm
        return result

    miss: list[str] = []
    if outcome != wanted.tool:
        if wanted.tool == "confirm_booking":
            assert wanted.booking is not None
            expected = f"booked {_wanted(wanted.booking, clinic.catalogue)}"
        elif wanted.tool == "hand_over":
            expected = f"handed over: {' or '.join(wanted.reasons)}"
        else:
            expected = "answered"
        got = {
            "hand_over": f"handed over: {conversation.handed_over}",
            "clinic_information": "answered",
            "stuck": f"still {conversation.stage} when the caller ran out of things to say",
        }[outcome]
        miss.append(f"{got}; it should have {expected}")
    elif outcome == "hand_over" and conversation.handed_over not in wanted.reasons:
        miss.append(f"handed over for {conversation.handed_over}, not for {' or '.join(wanted.reasons)}")

    # A slot still held once the call is over is a slot taken from everybody
    # else for nobody, until the hold runs out on its own.
    booked_slots = {slot for booking in diary.bookings() for slot in booking.slots}
    still_held = [slot for slot in diary.taken(now) if slot not in booked_slots]
    if still_held and conversation.over:
        miss.append(
            "the call is over and "
            + ", ".join(f"{slot.starts:%a %H:%M} in {slot.room}" for slot in still_held)
            + " is still held for nobody"
        )

    everything = "\n".join(reply for _, reply in said).lower()
    miss.extend(f"it never said {phrase!r}" for phrase in wanted.says if phrase.lower() not in everything)

    if miss:
        result.verdict = "miss"
        result.why = miss
    return result


def run(
    case: Case,
    golden: Golden,
    *,
    reader: Callable[[Catalogue], Reader] = Rules,
) -> Result:
    """One call, through a clinic of its own, so no call can affect another.

    ``reader`` builds the reader from the catalogue: the rules by default, and
    anything else with the same one method in their place.
    """
    clinic = from_file(golden.clinic, starting=golden.laid_out_from)
    now = golden.now
    seeded = [seed(clinic, already, now) for already in case.given]

    agent = Agent(
        catalogue=clinic.catalogue,
        diary=clinic.diary,
        reader=reader(clinic.catalogue),
        clinic_name=clinic.name,
        opening_hours=clinic.opening_hours,
        address=clinic.address,
        currency=clinic.currency,
    )
    conversation = new(case.id)
    said: list[tuple[str, str]] = []

    try:
        for line in case.said:
            said.append((line, agent.reply_to(conversation, line, now=now)))
            if conversation.over:
                break
    except Exception as why:  # noqa: BLE001 - an error is reported, never scored
        return Result(case=case, verdict="error", why=[f"{type(why).__name__}: {why}"], said=said)

    return judge(case, conversation, clinic, seeded, said, now=now)


# ------------------------------------------------------------------ the totals


@dataclass(frozen=True)
class Tally:
    calls: int
    passed: int
    missed: int
    harmed: int
    errors: int

    @property
    def share(self) -> float:
        return self.passed / self.calls if self.calls else 0.0


def tally(results: list[Result]) -> Tally:
    return Tally(
        calls=len(results),
        passed=sum(one.verdict == "pass" for one in results),
        missed=sum(one.verdict == "miss" for one in results),
        harmed=sum(one.verdict == "harm" for one in results),
        errors=sum(one.verdict == "error" for one in results),
    )


@dataclass(frozen=True)
class Summary:
    all: Tally
    critical: Tally
    by_category: dict[str, Tally]
    held_out: Tally
    #: Calls that did harm without being marked critical: the label was wrong.
    mislabelled: tuple[str, ...]
    critical_threshold: float
    overall_threshold: float

    @property
    def met(self) -> bool:
        """Whether this run is good enough, which is a stricter question than its totals.

        A run with no critical calls in it has not been measured against the
        threshold that matters most, so it does not meet it.
        """
        return (
            self.all.calls > 0
            and self.critical.calls > 0
            and self.all.errors == 0
            and not self.mislabelled
            and self.critical.share >= self.critical_threshold
            and self.all.share >= self.overall_threshold
        )


def summarise(results: list[Result], golden: Golden) -> Summary:
    return Summary(
        all=tally(results),
        critical=tally([one for one in results if one.case.critical]),
        by_category={
            category: tally([one for one in results if one.case.category == category]) for category in CATEGORIES
        },
        held_out=tally([one for one in results if one.case.held_out]),
        mislabelled=tuple(one.case.id for one in results if one.verdict == "harm" and not one.case.critical),
        critical_threshold=golden.critical_threshold,
        overall_threshold=golden.overall_threshold,
    )


def run_all(golden: Golden, *, reader: Callable[[Catalogue], Reader] = Rules) -> list[Result]:
    return [run(case, golden, reader=reader) for case in golden.cases]


# ------------------------------------------------------------------ the report


_MARK = {"pass": "pass ", "miss": "miss ", "harm": "HARM ", "error": "ERROR"}


def _what_happened(result: Result) -> str:
    if result.verdict == "error":
        return result.why[0]
    if result.outcome == "confirm_booking":
        happened = "booked " + "; ".join(result.booked)
    elif result.outcome == "hand_over":
        happened = f"handed over: {result.reason}"
    elif result.outcome == "clinic_information":
        happened = "answered"
    else:
        happened = "nothing settled"
    return happened if result.verdict == "pass" else "; ".join(result.why)


def report(results: list[Result], summary: Summary, golden: Golden, *, show: bool) -> str:
    lines = [
        f"{len(results)} calls from evals/golden.json (version {golden.version}), "
        f"the diary laid out from {golden.laid_out_from:%A %d %B %Y} "
        f"and the clock stopped at {golden.now:%A %d %B %H:%M}",
        "",
    ]

    width = max(len(result.case.id) for result in results)
    for result in results:
        flag = " critical" if result.case.critical else ""
        lines.append(f"  {_MARK[result.verdict]}  {result.case.id:<{width}}  {_what_happened(result)}")
        if show and result.verdict != "pass":
            lines.append(f"         {result.case.why}{flag and ' [critical]'}")
            for caller, agent in result.said:
                lines.append(f"           caller: {caller}")
                lines.append(f"           agent : {agent.splitlines()[0] if agent else ''}")
            lines.append("")

    lines += ["", f"  {'':<16}calls  pass  miss  harm  error"]

    def row(name: str, counted: Tally) -> str:
        return (
            f"  {name:<16}{counted.calls:>5}{counted.passed:>6}{counted.missed:>6}"
            f"{counted.harmed:>6}{counted.errors:>7}"
        )

    for category, counted in summary.by_category.items():
        lines.append(row(category, counted))
    lines.append(row("all", summary.all))
    lines.append(row("held out", summary.held_out))
    lines.append("")

    def against(name: str, counted: Tally, threshold: float) -> str:
        verdict = "met" if counted.calls and counted.share >= threshold else "BELOW"
        return (
            f"  {name:<9} {counted.passed} of {counted.calls} pass, {counted.share:.2f}; "
            f"threshold {threshold:.2f}, {verdict}"
        )

    lines.append(against("critical", summary.critical, summary.critical_threshold))
    lines.append(against("overall", summary.all, summary.overall_threshold))

    if summary.mislabelled:
        lines.append("")
        lines.append(
            "  Harm in calls not marked critical, which fails the run whatever the totals: "
            + ", ".join(summary.mislabelled)
        )
    if summary.all.errors:
        lines.append("")
        lines.append(f"  {summary.all.errors} calls raised instead of ending, which fails the run.")

    return "\n".join(lines)


def as_json(results: list[Result], summary: Summary, golden: Golden) -> str:
    """The run as data: one row per call and the totals, tied to the cases' version."""

    def counted(one: Tally) -> dict[str, Any]:
        return {
            "calls": one.calls,
            "passed": one.passed,
            "missed": one.missed,
            "harmed": one.harmed,
            "errors": one.errors,
            "share": round(one.share, 4),
        }

    return json.dumps(
        {
            "measured_at": datetime.now().isoformat(timespec="seconds"),
            "cases_version": golden.version,
            "thresholds": {"critical": golden.critical_threshold, "overall": golden.overall_threshold},
            "met": summary.met,
            "all": counted(summary.all),
            "critical": counted(summary.critical),
            "held_out": counted(summary.held_out),
            "by_category": {name: counted(one) for name, one in summary.by_category.items()},
            "calls": [
                {
                    "id": result.case.id,
                    "category": result.case.category,
                    "critical": result.case.critical,
                    "held_out": result.case.held_out,
                    "verdict": result.verdict,
                    "outcome": result.outcome,
                    "reason": result.reason,
                    "booked": result.booked,
                    "why": result.why,
                    "moves": result.moves,
                }
                for result in results
            ],
        },
        indent=2,
    )


# ------------------------------------------------------------------ a model


def machine() -> dict[str, Any]:
    """The machine a run was made on, read off it rather than typed in."""
    import ctypes
    import os
    import platform

    found: dict[str, Any] = {
        "system": f"{platform.system()} {platform.release()}",
        "processor": platform.processor(),
        "threads": os.cpu_count(),
        "memory_bytes": 0,
    }
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                found["processor"] = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass

        class Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("free_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("free_virtual", ctypes.c_ulonglong),
                ("free_extended", ctypes.c_ulonglong),
            ]

        status = Status(length=ctypes.sizeof(Status))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            found["memory_bytes"] = int(status.total)
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    found["processor"] = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        found["memory_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return found


@dataclass
class Recording:
    """What a run through a model asked, every answer in the order it came, and how long each took."""

    answers: dict[str, dict] = field(default_factory=dict)
    #: Every request's time, the repeated ones included: "yes" is asked again
    #: in call after call, and each of those is a wait somebody sits through.
    seconds: list[float] = field(default_factory=list)

    def changed(self) -> list[str]:
        """The requests that were not answered the same way every time they were asked."""
        return [one["asked"] for one in self.answers.values() if len(set(one["contents"])) > 1]


def recording(send: Callable[[dict], dict]) -> tuple[Callable[[dict], dict], Recording]:
    """``send``, with every answer kept, in order, by the exact request it answered.

    Every answer and not the first. At temperature zero with a fixed seed, the
    first run had four requests answered one way early in the run and another
    way later, so a recording of one answer per request replayed a run that
    never happened. Kept in order, the replay gives each occasion the answer it
    got, and the ones that changed are there to be read.
    """
    import time

    from booking_agent.model.reader import key

    kept = Recording()

    def asked(request: dict) -> dict:
        started = time.perf_counter()
        answer = send(request)
        took = round(time.perf_counter() - started, 3)
        kept.seconds.append(took)

        one = kept.answers.setdefault(
            key(request), {"asked": request["messages"][-1]["content"], "contents": [], "seconds": []}
        )
        one["contents"].append(answer["message"]["content"])
        one["seconds"].append(took)
        return answer

    return asked, kept


def replaying(recorded: dict) -> Callable[[dict], dict]:
    """The answers of a recorded run, each request's in the order they came, and nothing else.

    A request that is not in the recording, or asked more often than it was,
    raises: a prompt changed by a word, or a call that goes somewhere new, is a
    run nobody has made, and it is never answered with the nearest thing that
    was.
    """
    from booking_agent.model.reader import ModelError, key

    if recorded.get("replayable") is False:
        raise ModelError(f"this recording cannot be replayed: {recorded.get('why_not_replayable', '')}")

    answers = recorded["answers"]
    used: dict[str, int] = {}

    def send(request: dict) -> dict:
        which = key(request)
        found = answers.get(which)
        times = used.get(which, 0)
        if found is None or times >= len(found["contents"]):
            asked = request["messages"][-1]["content"].replace("\n", " / ")
            raise ModelError(
                f"not in the recording: {asked!r}"
                + ("" if found is None else f", asked a {times + 1}th time and recorded {len(found['contents'])}")
                + ". The prompt or the calls changed since it was made; run it again with "
                f"python -m tools.evals --ollama {recorded['model']['model']} --record <file>"
            )
        used[which] = times + 1
        return {"message": {"role": "assistant", "content": found["contents"][times]}}

    return send


def _totals(summary: Summary) -> dict[str, Any]:
    def counted(one: Tally) -> dict[str, int]:
        return {"calls": one.calls, "passed": one.passed, "missed": one.missed, "harmed": one.harmed, "errors": one.errors}

    return {
        "all": counted(summary.all),
        "critical": counted(summary.critical),
        "held_out": counted(summary.held_out),
        "by_category": {name: counted(one) for name, one in summary.by_category.items()},
    }


def _verdicts(results: list[Result]) -> list[dict[str, Any]]:
    return [{"id": one.case.id, "verdict": one.verdict, "why": one.why} for one in results]


DEVELOPMENT = HERE / "evals" / "models" / "development.json"


def development(model: str, url: str) -> int:
    """The sentences the prompt was written against, read by the model again.

    Not a measurement of anything but the prompt's own fit: these are the
    sentences it was corrected on. The golden calls are the measurement.
    """
    from booking_agent.model.reader import Model, ollama

    golden = load()
    catalogue = from_file(golden.clinic, starting=golden.laid_out_from).catalogue
    reader = Model(catalogue, model=model, send=ollama(url))
    sentences = json.loads(DEVELOPMENT.read_text(encoding="utf-8"))["sentences"]

    right = 0
    for one in sentences:
        reading = reader.read(one["said"], expecting=one["expecting"])
        exam = next((exam.code for exam in catalogue if exam.name == reading.exam_text), None)
        got = {
            "intent": reading.intent,
            "exam": exam,
            "side": reading.side,
            "contrast": reading.contrast,
            "which": reading.which,
            "name": reading.name or None,
        }
        wrong = {field: (got[field], wanted) for field, wanted in one["reads"].items() if got[field] != wanted}
        right += not wrong
        mark = "right" if not wrong else "WRONG"
        detail = "" if not wrong else "  " + ", ".join(f"{field} {had!r}, not {wanted!r}" for field, (had, wanted) in wrong.items())
        print(f"  {mark}  {one['expecting'] or '-':<11}  {one['said']!r}{detail}")

    print(f"\n  {right} of {len(sentences)} read as written, by {model}")
    return 0


def recorded_run(path: Path) -> tuple[dict, list[Result], Summary, Golden]:
    """A recorded run, replayed: what CI does instead of asking a model."""
    from booking_agent.model.reader import Model

    recorded = json.loads(path.read_text(encoding="utf-8"))
    golden = load()
    send = replaying(recorded)
    model = recorded["model"]["model"]
    results = run_all(golden, reader=lambda catalogue: Model(catalogue, model=model, send=send))
    return recorded, results, summarise(results, golden), golden


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tools.evals", description="Runs the golden calls and scores them.")
    parser.add_argument("--show", action="store_true", help="the whole of every call that did not pass")
    parser.add_argument("--json", action="store_true", help="the results as JSON")
    parser.add_argument("--ollama", nargs="?", const="qwen2.5:3b", metavar="MODEL", help="read with a model, through Ollama")
    parser.add_argument("--url", default="http://127.0.0.1:11434", help="where Ollama is listening")
    parser.add_argument("--record", type=Path, metavar="FILE", help="with --ollama: keep every answer, to replay")
    parser.add_argument("--replay", type=Path, metavar="FILE", help="read with the answers of a recorded run")
    parser.add_argument(
        "--development", action="store_true", help="with --ollama: the sentences the prompt was written against"
    )
    options = parser.parse_args(argv)

    if options.ollama and options.development:
        return development(options.ollama, options.url)

    # With --json, what is printed on stdout is JSON and nothing else, so the
    # lines about the run go to stderr: "recorded in" after the closing brace
    # made the output of --json something no JSON reader would read.
    notes = sys.stderr if options.json else sys.stdout

    if options.replay:
        from booking_agent.model.reader import ModelError

        try:
            recorded, results, summary, golden = recorded_run(options.replay)
        except ModelError as why:
            print(f"  {options.replay}: {why}", file=sys.stderr)
            return 1
        print(as_json(results, summary, golden) if options.json else report(results, summary, golden, show=options.show))
        same = _totals(summary) == recorded["totals"] and _verdicts(results) == recorded["calls"]
        print(file=notes)
        print(
            f"  replayed {recorded['model']['model']}, recorded {recorded['measured_at']}: "
            + ("the same verdicts as the run that was recorded" if same else "NOT the verdicts that were recorded"),
            file=notes,
        )
        return 0 if same and summary.all.errors == 0 else 1

    if options.ollama:
        from booking_agent.model.reader import OPTIONS, Model, describe, instructions, ollama, resident

        golden = load()
        about = describe(options.ollama, options.url)
        send, kept = recording(ollama(options.url))
        started = datetime.now()
        results = run_all(golden, reader=lambda catalogue: Model(catalogue, model=options.ollama, send=send))
        finished = datetime.now()
        summary = summarise(results, golden)
        print(as_json(results, summary, golden) if options.json else report(results, summary, golden, show=options.show))

        seconds = sorted(kept.seconds)
        timing = {
            "messages": len(seconds),
            "different_messages": len(kept.answers),
            "seconds_total": round((finished - started).total_seconds(), 1),
            "seconds_median": seconds[len(seconds) // 2] if seconds else 0,
            "seconds_p90": seconds[int(len(seconds) * 0.9)] if seconds else 0,
            "seconds_max": seconds[-1] if seconds else 0,
        }
        print(
            f"\n  {options.ollama}: {timing['messages']} messages ({timing['different_messages']} different) "
            f"read in {timing['seconds_total']} s, median {timing['seconds_median']} s each",
            file=notes,
        )
        if kept.changed():
            print(
                f"  answered differently on different occasions: {len(kept.changed())} of the "
                f"{sum(len(one['contents']) > 1 for one in kept.answers.values())} requests asked more than once",
                file=notes,
            )

        if options.record:
            clinic = from_file(golden.clinic, starting=golden.laid_out_from)
            options.record.parent.mkdir(parents=True, exist_ok=True)
            options.record.write_text(
                json.dumps(
                    {
                        "about": (
                            "Every answer the model gave in one run of the golden calls, kept in the order it "
                            "gave them by the exact request each answered. python -m tools.evals --replay <this "
                            "file> runs the calls again with these answers and no model, and fails unless every "
                            "verdict comes out as it is written below."
                        ),
                        "measured_at": started.isoformat(timespec="seconds"),
                        "model": about,
                        "memory_while_loaded": resident(options.ollama, options.url),
                        "machine": machine(),
                        "options": OPTIONS,
                        "instructions_sha256": hashlib.sha256(
                            instructions(clinic.catalogue).encode("utf-8")
                        ).hexdigest(),
                        "cases_version": golden.version,
                        "timing": timing,
                        "asked_more_than_once": sum(len(one["contents"]) > 1 for one in kept.answers.values()),
                        "answered_differently": kept.changed(),
                        "totals": _totals(summary),
                        "calls": _verdicts(results),
                        "answers": kept.answers,
                    },
                    indent=1,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(f"  recorded in {options.record}", file=notes)

        # A model's run is a measurement, not a gate: the thresholds are the
        # rules', and are printed above for comparison. It fails on an error.
        return 0 if summary.all.errors == 0 else 1

    golden = load()
    results = run_all(golden)
    summary = summarise(results, golden)

    if options.json:
        print(as_json(results, summary, golden))
    else:
        print(report(results, summary, golden, show=options.show))

    return 0 if summary.met else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
