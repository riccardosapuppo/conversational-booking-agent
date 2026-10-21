"""What a language model makes of one message, asked for as the fields of a Reading.

The rules in conversation/reading.py are lists of words, and the golden calls
found where lists of words stop: "XR knee rt" names the exam by the code of its
modality, and "C-" is radiology shorthand that disappears with the punctuation.
This reader asks a model instead, through an Ollama server on this machine, and
asks it for what the rules produce: a Reading, filled in, and not a reply.

Three things keep it a reader rather than an agent.

It fills in fields and decides nothing. The JSON it may answer in is fixed by a
schema the server holds the model to, so the intent is one of the eleven, the
exam is one of the catalogue's codes or none, and the side is left, right, both
or none: a model held to the schema cannot name an exam the clinic does not
have. What it fills in then goes through the graph, the catalogue and the
diary, which refuse what they refused before.

It is told what the agent asked, as the rules are, and nothing more. The
protocol is one message and the question it answers, and a model handed the
whole call would be measured on something the rules never had.

It is asked at temperature zero with a fixed seed, and what it answered can be
recorded and replayed: that is how CI puts the golden calls through it with no
model and no network, from the answers of a run on a real one, looked up by the
exact request that produced them. A request that is not in the recording is an
error rather than a guess, so a prompt changed by a word is a run that has not
been made yet.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Callable, get_args

from booking_agent.clinic.catalogue import Catalogue, Side
from booking_agent.conversation.reading import Intent, Reading

#: The model asked when none is named: one that fits a machine with 8 GB.
DEFAULT_MODEL = "qwen2.5:3b"

#: Where Ollama listens when it is started with nothing configured.
DEFAULT_URL = "http://127.0.0.1:11434"

INTENTS: tuple[str, ...] = get_args(Intent)
SIDES: tuple[str, ...] = get_args(Side)

#: The agent's last question, in the words the model is given, and how an
#: answer to it reads. The same values the graph passes the rules as
#: ``expecting``; one it does not know is an error, because a question
#: described wrongly is a reading measured wrongly. The guidance sits next to
#: the message rather than in the instructions: written there, a side said on
#: its own was read as picking an option, because it ended in "one".
QUESTIONS = {
    "slot": (
        'up to three numbered times were read out, then "Which suits you?"',
        "A number or a position is choose, with which. Turning them all down, or asking for "
        "other times, is refuse.",
    ),
    "confirm": (
        'a booking was read back, then "Shall I book that?"',
        "Yes is agree and no is refuse. A side or a contrast said with it goes in side and contrast.",
    ),
    "exam_choice": (
        '"Did you mean ... or ...?", naming two or three exams',
        "Naming one of them is book, with its exam. A position is choose, with which.",
    ),
    "side": (
        '"Left or right?"',
        "A side is book, with that side, even said with the word one after it: that is not choose.",
    ),
    "contrast": (
        '"With or without contrast?"',
        "Yes, or with, is book with contrast true. No, or without, is book with contrast false. "
        "Not knowing is unclear.",
    ),
    "patient": (
        '"What name should I put it under?"',
        "A person's name is book, with name, and exam and exam_words null.",
    ),
    "": ("none in particular: the call has just started, or the caller has moved on", ""),
}

#: What an answer to each question can be, held to by the schema as the exam
#: codes are. Picking an option when none had been offered was the commonest
#: wrong answer while the prompt was written, so "choose" exists only where
#: there is something to choose from, as the rules only read an ordinal there.
_ALWAYS = ("urgent", "clinical", "operator", "manage", "unclear", "info")
ANSWERS = {
    "slot": _ALWAYS + ("choose", "refuse", "agree", "book"),
    "confirm": _ALWAYS + ("agree", "refuse", "book"),
    "exam_choice": _ALWAYS + ("choose", "refuse", "book"),
    "side": _ALWAYS + ("book",),
    "contrast": _ALWAYS + ("book",),
    "patient": _ALWAYS + ("book",),
    "": _ALWAYS + ("book", "agree", "refuse", "greeting"),
}

#: How the server is asked to answer: the same every time, so the same request
#: gets the same answer and a recording of one can stand in for it.
OPTIONS = {"temperature": 0, "seed": 7, "num_ctx": 2048}

#: One request in, one response out, both as the JSON Ollama speaks. The reader
#: knows nothing about where it goes: a server, a recording, or a test.
Send = Callable[[dict], dict]

_INSTRUCTIONS = """\
You read one message from a caller to the booking line of a diagnostic imaging \
clinic, and you fill in a JSON object saying what it means. You do not answer \
the caller and you decide nothing: the booking system does that with what you \
fill in. When you are not sure, the intent is unclear. A wrong guess books the \
wrong scan; unclear passes the call to a person.

The clinic's exams:
{exams}

The intents:
- urgent: may be an emergency now: pain or tightness in the chest, dizziness, \
trouble breathing, a heart attack or a stroke, fainting or collapsing, \
bleeding, an injury or a fall, a bone that may be broken, severe pain.
- clinical: a clinician must weigh it before anything is booked: pregnancy \
or breastfeeding, kidney trouble or dialysis, an allergy or a reaction to \
contrast, a pacemaker, a defibrillator, an implant or metal in the body, or a \
question about medication.
- operator: asks to talk to a person: a human, an operator, the reception, a \
member of staff. Asking the line to wait is not this.
- manage: about an appointment they already have: cancelling, moving, \
changing or checking it.
- unclear: not knowing or not being sure, a filler or a request to wait, \
noise, a question back, or anything that could mean two different things.
- agree: yes to the question just asked. refuse: no to it.
- choose: picks one of the options just offered, by number or by position.
- info: a question about the clinic or the visit (opening hours, address, \
parking, preparation, who may come along), or what an exam costs, without \
asking to book.
- book: says what they want booked, by naming or describing an exam, or \
gives its side, its contrast or the name asked for.
- greeting: only a greeting.
urgent and clinical come before anything else the message also says.

The other fields are null when the message does not say:
- exam: the code of the exam, only when the message says which kind it is \
(MRI, X-ray, ultrasound, CT, mammogram) or uses one of its other names. A body \
part alone can be more than one exam: null, with the words in exam_words.
- exam_words: the caller's own words that name an exam or a body part to scan; \
null for anything else.
- side: left, right or both, said of the body part. "Not the left" is right. \
Null for "right" meaning correct, and null when two sides are said and \
neither is taken back.
- contrast: true if they want contrast; false for no contrast, without, w/o, \
plain or contrast-free; null if it is not said or not known.
- which: the number picked, counting from 1, when the intent is choose.
- name: the name given, only when the question was the name and the message \
is a name.
- concern: the caller's words that made the intent urgent or clinical.

Each message comes with the question the caller is answering and how an \
answer to it reads. Some examples, with the fields that are not null:
- no question; "could I book a mammogram for next week" -> intent book, exam MAM-SCREEN
- no question; "could you fit me in for a knee x-ray" -> intent book, exam XR-KNEE
- no question; "it's my knee that needs looking at" -> intent book, exam_words "knee"
- no question; "what's the address of the clinic" -> intent info
- no question; "how much is the spine MRI" -> intent info, exam MRI-SPINE
- no question; "I'd like to change the time of my appointment" -> intent manage
- no question; "can I talk to an actual person" -> intent operator
- no question; "I've had a fall and my ankle is swollen and very painful" -> \
intent urgent, concern "had a fall and my ankle is swollen and very painful"
- no question; "is it safe if I'm breastfeeding" -> intent clinical, concern \
"breastfeeding"
- no question; "erm, just a moment" -> intent unclear
- "Left or right?"; "on the right, please" -> intent book, side right
- "With or without contrast?"; "without please" -> intent book, contrast false
- "With or without contrast?"; "not sure really" -> intent unclear
- times read out; "the middle one is fine" -> intent choose, which 2
- times read out; "none of those, sorry" -> intent refuse
- "Shall I book that?"; "yes please do" -> intent agree
- "Shall I book that?"; "yes, and with contrast" -> intent agree, contrast true
- "Shall I book that?"; "no, leave it" -> intent refuse
- "Did you mean ... or ...?"; "the ultrasound" -> intent book, exam US-ABDOMEN
- "What name should I put it under?"; "Paola Moretti" -> intent book, name \
"Paola Moretti\""""


class ModelError(RuntimeError):
    """The model could not be asked, or answered outside what it was allowed to."""


def instructions(catalogue: Catalogue) -> str:
    """The system prompt: the task, this clinic's exams, and what each field means.

    The exams are written out from the catalogue in use, with their other
    names and what each needs, so the model is shown the clinic it is reading
    for and not one somebody once typed in.
    """
    lines = []
    for exam in catalogue:
        line = f"- {exam.code}: {exam.name}"
        if exam.synonyms:
            line += f" (also {', '.join(exam.synonyms)})"
        needs = [what for what, needed in (("a side", exam.needs_side), ("with or without contrast", exam.needs_contrast)) if needed]
        if needs:
            line += "; needs " + " and ".join(needs)
        if not exam.bookable:
            line += "; cannot be booked on this line"
        lines.append(line)
    return _INSTRUCTIONS.format(exams="\n".join(lines))


def schema(catalogue: Catalogue, expecting: str = "") -> dict[str, Any]:
    """The only JSON the model may answer in, given the question it is answering.

    Held to by the server rather than asked for in the prompt: an exam code
    outside the catalogue cannot be written at all, and nor can an intent the
    question does not allow.
    """

    def either(*kinds: str) -> dict[str, Any]:
        return {"type": [*kinds, "null"]}

    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": [one for one in INTENTS if one in ANSWERS[expecting]]},
            "concern": either("string"),
            "exam": {"anyOf": [{"type": "string", "enum": [exam.code for exam in catalogue]}, {"type": "null"}]},
            "exam_words": either("string"),
            "side": {"anyOf": [{"type": "string", "enum": list(SIDES)}, {"type": "null"}]},
            "contrast": either("boolean"),
            "which": either("integer"),
            "name": either("string"),
        },
        "required": ["intent", "concern", "exam", "exam_words", "side", "contrast", "which", "name"],
        "additionalProperties": False,
    }


def key(request: dict) -> str:
    """What identifies a request exactly: every word of the prompt included."""
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def ollama(url: str = DEFAULT_URL, *, timeout: float = 300.0) -> Send:
    """Sends a request to an Ollama server's chat endpoint, and nowhere else.

    Through the standard library, so this reader brings no dependency with it.
    """
    endpoint = url.rstrip("/") + "/api/chat"

    def send(request: dict) -> dict:
        body = json.dumps(request).encode("utf-8")
        asked = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(asked, timeout=timeout) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as why:
            detail = why.read().decode("utf-8", "replace")[:300]
            raise ModelError(f"{endpoint} answered {why.code}: {detail}") from why
        except (urllib.error.URLError, OSError) as why:
            raise ModelError(f"nothing answered at {endpoint} ({why}); is Ollama running?") from why

    return send


def _get(url: str, path: str, *, timeout: float = 10.0) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as why:
        raise ModelError(f"nothing answered at {url}{path} ({why}); is Ollama running?") from why


def describe(model: str, url: str = DEFAULT_URL) -> dict[str, Any]:
    """Which Ollama, and which weights exactly, from the server rather than from memory."""
    version = _get(url, "/api/version").get("version", "")
    for one in _get(url, "/api/tags").get("models", []):
        if one.get("name") == model or one.get("model") == model:
            details = one.get("details", {})
            return {
                "ollama": version,
                "model": model,
                "digest": one.get("digest", ""),
                "bytes": one.get("size", 0),
                "parameters": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
            }
    raise ModelError(f"{model!r} is not on the Ollama server at {url}: ollama pull {model}")


def resident(model: str, url: str = DEFAULT_URL) -> dict[str, int]:
    """How much memory the model takes while it is loaded, as the server reports it."""
    for one in _get(url, "/api/ps").get("models", []):
        if one.get("name") == model or one.get("model") == model:
            return {"bytes": int(one.get("size", 0)), "in_vram_bytes": int(one.get("size_vram", 0))}
    return {"bytes": 0, "in_vram_bytes": 0}


class Model:
    """A Reader that asks a language model, through ``send``, for the fields of a Reading."""

    def __init__(self, catalogue: Catalogue, *, model: str = DEFAULT_MODEL, send: Send | None = None) -> None:
        # The exam the model names is handed on by its name, so every name has
        # to find its own exam and no other: in a catalogue where it did not,
        # the model could say one exam and the graph book another.
        for exam in catalogue:
            found = catalogue.resolve(exam.name)
            if found is None or found.exam != exam:
                raise ModelError(f"{exam.name!r} does not find {exam.code} in this catalogue")
        self._catalogue = catalogue
        self.model = model
        self._send = send or ollama()
        self._instructions = instructions(catalogue)
        self._schemas = {expecting: schema(catalogue, expecting) for expecting in QUESTIONS}

    def request(self, text: str, *, expecting: str = "") -> dict:
        """What is sent for one message: the same message and question, the same request."""
        if expecting not in QUESTIONS:
            raise ModelError(f"no description of the question {expecting!r}")
        question, how = QUESTIONS[expecting]
        said = f"The question the caller is answering: {question}\n"
        if how:
            said += f"How an answer to it reads: {how}\n"
        said += f"The caller said: {text.strip()}"
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._instructions},
                {"role": "user", "content": said},
            ],
            "format": self._schemas[expecting],
            "options": OPTIONS,
            "stream": False,
        }

    def read(self, text: str, *, expecting: str = "") -> Reading:
        if not text.strip():
            # Nothing said is not understood, as it is for the rules, and it
            # is not worth asking a model about.
            return Reading(intent="unclear")

        answer = self._send(self.request(text, expecting=expecting))
        try:
            content = answer["message"]["content"]
            fields = json.loads(content)
        except (KeyError, TypeError, ValueError) as why:
            raise ModelError(f"the answer was not the JSON asked for: {str(answer)[:300]}") from why
        return self._reading(fields, expecting=expecting)

    def _reading(self, fields: Any, *, expecting: str) -> Reading:
        """The answer, checked against the schema again, as a Reading.

        Checked here as well as by the server: a recording can be edited and a
        server can be another one, and a reading the graph should never see is
        an error, not a guess at what was meant.
        """
        if not isinstance(fields, dict):
            raise ModelError(f"the answer was {fields!r}, not an object")
        allowed = self._schemas[expecting]
        missing = sorted(set(allowed["required"]) - set(fields))
        extra = sorted(set(fields) - set(allowed["properties"]))
        if missing or extra:
            raise ModelError(f"the answer had {missing or 'nothing'} missing and {extra or 'nothing'} extra")

        intent = fields["intent"]
        if intent not in allowed["properties"]["intent"]["enum"]:
            raise ModelError(f"{intent!r} is not an answer to the question {expecting!r}")

        code = fields["exam"]
        exam = self._catalogue.get(code) if code is not None else None
        if code is not None and exam is None:
            raise ModelError(f"{code!r} is not an exam this clinic has")

        side = fields["side"]
        if side is not None and side not in SIDES:
            raise ModelError(f"{side!r} is not a side")

        contrast = fields["contrast"]
        if contrast is not None and not isinstance(contrast, bool):
            raise ModelError(f"contrast was {contrast!r}")

        which = fields["which"]
        if which is not None and (isinstance(which, bool) or not isinstance(which, int)):
            raise ModelError(f"which was {which!r}")

        for name in ("exam_words", "name", "concern"):
            if fields[name] is not None and not isinstance(fields[name], str):
                raise ModelError(f"{name} was {fields[name]!r}")

        # The exam by its name in the catalogue, which resolves to exactly that
        # exam; the caller's words only when the model would not say which,
        # so that two exams equally fitting are asked about, as with the rules.
        exam_text = exam.name if exam is not None else (fields["exam_words"] or "").strip()

        # A name only when a name was asked for, as with the rules: "Mario" in
        # the middle of a booking is not necessarily the patient.
        name = (fields["name"] or "").strip() if expecting == "patient" else ""

        return Reading(
            intent=intent,
            exam_text=exam_text,
            side=side,
            contrast=contrast,
            which=which if intent == "choose" else None,
            name=name,
            concern=(fields["concern"] or "").strip(),
        )
