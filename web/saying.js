/**
 * Saying the reply out loud, when the call is on the telephone.
 *
 * The two channels were a caption. Choosing "the telephone" changed the words
 * in the bubble — a list became sentences, "09:00" became "nine o'clock in the
 * morning", a reference was spelled out and then spelled again — and somebody
 * looking at the page went on typing and reading, so the one question the page
 * got asked was what the difference was supposed to be. All that work was
 * real, and none of it was audible, which for a telephone channel is the whole
 * of the problem.
 *
 * The browser can already speak. No key, no account, no permission and no
 * request: `speechSynthesis` is the machine's own voices, and a reply said out
 * loud is the difference between reading that a reference is repeated and
 * hearing it repeated while you write it down.
 *
 * ── The four things that make this bearable ─────────────────────────────────
 *
 * 1. **It can be silenced, and the silence is remembered.** Somebody who opens
 *    a page that starts talking closes it, and having to silence it again on
 *    every reload is the same insult twice. The choice is kept per browser.
 *
 * 2. **Nothing is ever said without somebody having asked for it.** Browsers
 *    refuse speech until a page has been interacted with, and a feature that
 *    half works is worse than one that is off — so this is never called on the
 *    way in. The greeting spoken when somebody picks the telephone, or presses
 *    "start again", is spoken because they pressed something. The one on
 *    arrival is not spoken at all, and that is the correct behaviour rather
 *    than a workaround for the policy.
 *
 * 3. **It stops when the conversation moves.** A new sentence, a new call, or
 *    the sound being turned off cuts off whatever is being said. Speech that
 *    queues up behind itself is a page talking about the message before last.
 *
 * 4. **The voices arrive late.** `getVoices()` is empty on the first call in
 *    every browser here and fills in when the platform gets round to it, so
 *    the choice is made again on `voiceschanged` and nothing waits for it:
 *    with no voice chosen the utterance carries a language and the platform
 *    picks. A page that held its tongue until a list arrived would be silent
 *    for exactly the first reply anybody hears.
 *
 * Where there is no `speechSynthesis` — and it is a real browser that has
 * none, not a hypothetical one — nothing here runs, the control is not shown,
 * and the page is exactly what it was.
 */

/** Where the choice is kept. Per browser, because that is whose ears they are. */
const KEY = 'booking-agent:sound';

/**
 * The language of everything the agent says.
 *
 * Set on the utterance as well as used to choose a voice: it is what a
 * platform falls back on when the voice list has not arrived, and without it
 * an English sentence is read out by whatever voice the machine happens to
 * have first — which on a machine set up in another language is a very
 * confident reading of the wrong words.
 */
const LANGUAGE = 'en-GB';

/**
 * A shade under natural pace.
 *
 * The reason is in `voice.py`: this is written for somebody with a pen. A
 * reference read at conversational speed is a reference read twice for
 * nothing.
 */
const PACE = 0.95;

export const can = typeof speechSynthesis !== 'undefined' && typeof SpeechSynthesisUtterance === 'function';

let on = remembered();
let chosen = null;

/**
 * Storage that is allowed to be missing.
 *
 * Private windows, cleared site data, and settings that block it: reading it
 * can throw rather than return nothing. A page that fell over on that would
 * fall over for the people most likely to be careful about what a page does.
 */
function remembered() {
  try {
    return localStorage.getItem(KEY) !== 'off';
  } catch {
    return true;
  }
}

function remember() {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    // Nothing to do about it, and nothing worth telling anybody: the sound is
    // right for this visit and will be on again next time.
  }
}

/** An English voice, if the platform has got round to saying what it has. */
function pick() {
  const all = can ? speechSynthesis.getVoices() : [];

  return (
    all.find((voice) => voice.lang === LANGUAGE) ||
    all.find((voice) => voice.lang?.replace('_', '-').startsWith('en')) ||
    null
  );
}

if (can) {
  chosen = pick();

  // They arrive after the page does, always, on every browser tried here.
  speechSynthesis.addEventListener?.('voiceschanged', () => {
    chosen = pick();
  });

  // Speech outlives the page that started it: leave while a reference is being
  // read out and the reference goes on being read out.
  addEventListener('pagehide', () => speechSynthesis.cancel());
}

export function isOn() {
  return on;
}

/** Whether it should speak, remembered for next time. Turning it off is silence now. */
export function turn(wanted) {
  on = Boolean(wanted);
  remember();

  if (!on) hush();
  return on;
}

export function hush() {
  if (can) speechSynthesis.cancel();
}

/**
 * Says it, cutting off whatever was being said.
 *
 * :returns: whether anything was asked for, which is what the page needs to
 *     know and is not the same as whether anything was heard — the speaking is
 *     the platform's, and it does not report back.
 */
export function say(text) {
  if (!can || !on) return false;

  const words = String(text ?? '').trim();
  if (!words) return false;

  hush();

  const utterance = new SpeechSynthesisUtterance(words);
  utterance.lang = LANGUAGE;
  utterance.rate = PACE;

  if (!chosen) chosen = pick();
  if (chosen) utterance.voice = chosen;

  speechSynthesis.speak(utterance);
  return true;
}
