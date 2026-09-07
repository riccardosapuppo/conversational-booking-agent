/**
 * The console, in plain JavaScript.
 *
 * No framework and no build step. This page exists so somebody can watch the
 * agent work for five minutes; a toolchain between them and that is a toolchain
 * for nothing — and a service whose own demonstration needed one would be
 * quietly arguing that its API is hard to call.
 *
 * Everything here is `fetch` against the endpoints in the README. Nothing on
 * this page books anything: the conversation does that, through the same rules
 * a telephone call goes through, and a screen with its own path to the diary
 * would be the path nobody tested.
 */

const $ = (id) => document.getElementById(id);

/** The call in progress, if there is one. */
let call = null;

// ──────────────────────────────────────────────── which side of the desk

/** The two views, and the only two things the address will be believed about. */
const SIDES = ['caller', 'desk'];

/**
 * Show one side, and write it down where a reload will find it.
 *
 * In the address rather than in storage, because it is the sort of thing
 * somebody sends to a colleague: a link that opens on the side you were looking
 * at is worth more than one that opens wherever their own browser last was.
 * A reload keeps it either way, which is the part that was actually asked for.
 */
function showSide(wanted) {
  const chosen = SIDES.includes(wanted) ? wanted : SIDES[0];

  for (const button of document.querySelectorAll('[data-side]')) {
    const here = button.dataset.side === chosen;
    button.setAttribute('aria-selected', String(here));
    $(`side-${button.dataset.side}`).hidden = !here;
  }

  // `replaceState` rather than assigning the hash: switching sides is not
  // somewhere to come back to with the back button, and a history entry per
  // click would bury whatever page somebody arrived from.
  if (location.hash !== `#${chosen}`) {
    history.replaceState(null, '', `#${chosen}`);
  }
}

for (const button of document.querySelectorAll('[data-side]')) {
  button.addEventListener('click', () => {
    showSide(button.dataset.side);
    // Coming to the caller's side is coming to say something, and the one
    // thing on it that takes typing is the thing to be typing into.
    if (button.dataset.side === 'caller') $('text').focus();
  });
}

window.addEventListener('hashchange', () => showSide(location.hash.slice(1)));

showSide(location.hash.slice(1));

// ───────────────────────────────────────────────────────────── the shell

async function readClinic() {
  try {
    const clinic = await (await fetch('/clinic')).json();

    $('clinic-name').textContent = clinic.name;
    $('clinic-detail').textContent = `${clinic.opening_hours} · ${clinic.address}`;
    $('count-exams').textContent = clinic.exams;
    $('count-rooms').textContent = clinic.rooms.length;
  } catch {
    $('clinic-detail').textContent = 'the service is not answering';
  }
}

/**
 * What is doing the reading, asked of the service rather than written here.
 *
 * The page used to assert "no model, no key" in prose it had no way of being
 * wrong about, which is the same as asserting nothing. This asks `/reading`,
 * which answers from the object actually in use — so a model-backed reader
 * would be named here by a page that knows nothing about models.
 */
async function readWhatIsReading() {
  try {
    const reading = await (await fetch('/reading')).json();
    const wide = reading.seam.methods.length;

    $('reader-name').textContent = reading.reader;
    $('reader-where').textContent = `${reading.module.replaceAll('.', '/')}.py`;
    $('reader-default').textContent = reading.default
      ? 'the reader this service builds when nobody hands it another. Rules, so it runs with no key, no account and nothing to sign up for.'
      : 'handed to the service when it started, in place of the rules.';

    $('seam-protocol').textContent = reading.seam.protocol;
    $('seam-methods').textContent = reading.seam.methods.map((one) => `${one}()`).join(', ');
    $('seam-wide').textContent = wide === 1 ? 'one method' : `${wide} methods`;
  } catch {
    // Left as an ellipsis this reads as "still loading" for ever, which is the
    // one thing a strip about honesty should not do.
    $('reader-name').textContent = 'nothing';
    $('reader-where').textContent = '—';
    $('reader-default').textContent = 'the service is not answering, and this page will not guess on its behalf.';
  }
}

// ─────────────────────────────────────────────────────── the conversation

/**
 * One bubble in the transcript, empty, already on the screen.
 *
 * Appended rather than redrawn from a list: the point of the whole exercise is
 * that a bubble can exist before the words that go in it do, and a function
 * that rebuilds the transcript from what is known would have nothing to say
 * about a reply that has not arrived.
 */
function bubble(who) {
  const line = document.createElement('li');
  line.dataset.who = who;

  const label = document.createElement('span');
  label.className = 'who';
  label.textContent = who === 'you' ? 'you' : 'the agent';

  const words = document.createElement('div');
  words.className = 'words';

  line.append(label, words);
  $('said').append(line);
  line.scrollIntoView({ block: 'nearest' });

  return words;
}

/**
 * The reply's bubble, put up before there is a reply to put in it.
 *
 * This is the one behaviour worth copying from the chat this project came out
 * of. Nothing here is faster for it — the agent takes exactly as long as it
 * took — but somebody who has just pressed a button can see that their message
 * left and that something is now happening, instead of watching a screen that
 * did nothing at all and pressing it again.
 */
function waiting() {
  const words = bubble('agent');
  words.classList.add('thinking');

  const loader = document.createElement('span');
  loader.className = 'loader';
  loader.setAttribute('role', 'status');
  loader.setAttribute('aria-label', 'working out what to say');
  loader.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));

  words.append(loader);
  return words;
}

/** The words, in place of the loader. Whatever happens, the loader goes. */
function fill(words, text) {
  words.classList.remove('thinking');
  words.textContent = text;
  words.parentElement.scrollIntoView({ block: 'nearest' });
}

function showStage(reply) {
  $('stage').hidden = false;
  $('stage-name').textContent = reply.stage;
  $('call-reference').textContent = reply.call.slice(0, 8);

  // A handover is not an error and is not drawn as one. It is the agent doing
  // the most useful thing available to it, and the reason is the whole point.
  if (reply.handed_over) {
    $('handover').hidden = false;
    $('handover').textContent = `Handed to a person — ${reply.handed_over}${
      reply.handover_note ? `: ${reply.handover_note}` : ''
    }`;
  } else {
    $('handover').hidden = true;
  }
}

/**
 * Start a call, and greet into a bubble that is already waiting.
 *
 * `clearing` is false in one place only: a call let go of after an hour, where
 * the note saying so is the last thing in the transcript and wiping it would
 * leave somebody looking at a greeting they did not ask for.
 */
async function startACall({ clearing = true } = {}) {
  if (clearing) $('said').replaceChildren();

  const first = waiting();

  try {
    const response = await fetch('/calls', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: $('channel').value }),
    });

    const reply = await response.json();

    call = reply.call;
    fill(first, reply.reply);
    showStage(reply);
    return reply;
  } catch {
    // A loader that spins for ever is a worse answer than a bad one: it says
    // "still working" forever about something that has stopped.
    call = null;
    fill(first, 'The service did not answer, so there is no call to have.');
    return null;
  }
}

async function say(words) {
  if (!words.trim()) return;

  // Before the bubbles, so a first message does not appear above the greeting
  // it is answering.
  if (!call) await startACall();
  if (!call) return;

  bubble('you').textContent = words;
  const answer = waiting();

  let reply;

  try {
    const response = await fetch(`/calls/${encodeURIComponent(call)}/said`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: words }),
    });

    if (response.status === 404) {
      // An hour of silence and the call is let go of. Starting a new one is the
      // right answer, and saying nothing at all would leave somebody typing
      // into a conversation that has stopped existing.
      fill(answer, 'That call has been let go of. Starting a new one.');
      call = null;
      await startACall({ clearing: false });
      return;
    }

    reply = await response.json();
    fill(answer, reply.reply);
  } catch {
    fill(answer, 'The service did not answer. Nothing was said to the agent.');
    return;
  }

  showStage(reply);

  // Anything the agent does to the clinic shows up on the other side straight
  // away: a slot held during a call stops being offered, and a booking appears.
  await Promise.all([showDiary(), showBookings()]);
}

$('say-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const words = $('text').value;
  $('text').value = '';
  $('send').disabled = true;

  try {
    await say(words);
  } finally {
    $('send').disabled = false;
    $('text').focus();
  }
});

for (const button of document.querySelectorAll('[data-say]')) {
  button.addEventListener('click', () => void say(button.dataset.say));
}

$('again').addEventListener('click', async () => {
  if (call) await fetch(`/calls/${encodeURIComponent(call)}`, { method: 'DELETE' }).catch(() => {});
  call = null;
  $('handover').hidden = true;
  await startACall();
  await Promise.all([showDiary(), showBookings()]);
});

$('channel').addEventListener('change', async () => {
  $('channel-says').textContent =
    $('channel').value === 'voice'
      ? 'On the telephone a time is spoken rather than listed, and a reference is spelled out twice — because somebody is writing it down.'
      : 'In chat the times are a numbered list, because somebody can read them back.';

  // A channel is a property of a call, not a setting: changing it starts a new
  // one rather than switching how the current one is spoken half way through.
  if (call) await fetch(`/calls/${encodeURIComponent(call)}`, { method: 'DELETE' }).catch(() => {});
  call = null;
  await startACall();
});

// ────────────────────────────────────────────────────────────── the clinic

/**
 * Escaped, because half of what appears here is text somebody typed.
 *
 * The agent's replies are built from the catalogue and the diary and are not
 * dangerous; what a visitor types is echoed straight back, and a console that
 * put it into the page as markup would be a small hole in a page anybody can
 * open. The transcript builds its bubbles out of nodes and needs none of this;
 * the lists below are strings, and do.
 */
function escaped(text) {
  const box = document.createElement('span');
  box.textContent = String(text ?? '');
  return box.innerHTML;
}

for (const tab of document.querySelectorAll('[data-tab]')) {
  tab.addEventListener('click', () => {
    for (const other of document.querySelectorAll('[data-tab]')) {
      const chosen = other === tab;
      other.setAttribute('aria-selected', String(chosen));
      $(`tab-${other.dataset.tab}`).hidden = !chosen;
    }
  });
}

async function showCatalogue(q = '') {
  const said = await (await fetch(`/catalogue?q=${encodeURIComponent(q)}`)).json();

  $('exams').innerHTML = said.exams
    .map((exam) => {
      const asks = [
        exam.needs_side ? 'asks which side' : null,
        exam.needs_contrast ? 'asks about contrast' : null,
      ].filter(Boolean);

      return `<li data-bookable="${exam.bookable}">
        <div class="line">
          <span class="code mono">${escaped(exam.code)}</span>
          <span class="name">${escaped(exam.name)}</span>
          <span class="minutes">${exam.minutes} min</span>
        </div>
        <div class="line small">
          <span class="modality">${escaped(exam.modality)}</span>
          ${asks.length ? `<span class="asks">${asks.join(', ')}</span>` : ''}
          ${exam.synonyms.length ? `<span class="also">also: ${escaped(exam.synonyms.join(', '))}</span>` : ''}
        </div>
        ${
          exam.bookable
            ? ''
            : `<p class="not-bookable">Not bookable by the agent${
                exam.unbookable_reason ? ` — ${escaped(exam.unbookable_reason)}` : ''
              }</p>`
        }
        ${
          said.searched && exam.matched?.length
            ? `<p class="matched">found on: ${escaped(exam.matched.join(', '))}</p>`
            : ''
        }
      </li>`;
    })
    .join('');

  if (said.exams.length === 0) {
    $('exams').innerHTML = `<li class="nothing">Nothing in the catalogue answers to that.</li>`;
  }
}

$('find-form').addEventListener('submit', (event) => {
  event.preventDefault();
  void showCatalogue($('find').value);
});

async function showDiary() {
  const minutes = $('minutes').value;
  const said = await (await fetch(`/diary?days=5&minutes=${minutes}`)).json();

  $('days').innerHTML = said.days
    .map((day) => {
      if (day.rooms.length === 0) {
        return `<div class="day empty">
          <h3>${escaped(day.weekday)} <span class="mono">${escaped(day.day)}</span></h3>
          <p class="nothing">Nothing free for ${said.for_minutes} minutes.</p>
        </div>`;
      }

      return `<div class="day">
        <h3>${escaped(day.weekday)} <span class="mono">${escaped(day.day)}</span></h3>
        ${day.rooms
          .map(
            (room) => `<div class="room">
              <span class="room-name mono">${escaped(room.room)}</span>
              <span class="times">${room.free
                .map((at) => `<span class="at">${escaped(at.slice(11))}</span>`)
                .join('')}${room.more ? `<span class="more">+${room.more}</span>` : ''}</span>
            </div>`
          )
          .join('')}
      </div>`;
    })
    .join('');
}

$('minutes').addEventListener('change', () => void showDiary());
$('refresh').addEventListener('click', () => void showDiary());

async function showBookings() {
  const said = await (await fetch('/bookings')).json();

  $('count-booked').textContent = said.bookings.length;

  $('bookings').innerHTML = said.bookings.length
    ? said.bookings
        .map(
          (one) => `<li>
            <span class="reference mono">${escaped(one.reference)}</span>
            <span class="patient">${escaped(one.patient)}</span>
            <span class="what">${escaped(one.exams.join(', '))}</span>
            <span class="when mono">${escaped(one.starts.replace('T', ' '))}</span>
            <span class="room mono">${escaped(one.room)}</span>
          </li>`
        )
        .join('')
    : `<li class="nothing">Nothing booked yet. Book something on the caller's side.</li>`;
}

// ──────────────────────────────────────────────────────────────── on arrival

await Promise.all([readClinic(), readWhatIsReading()]);
await Promise.all([showCatalogue(), showDiary(), showBookings()]);
await startACall();

// Only if it is on the screen: focusing something inside a hidden panel puts
// the caret nowhere and scrolls nothing, which looks exactly like a bug.
if (!$('side-caller').hidden) $('text').focus();
