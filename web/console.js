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

/** What was said, so a redraw does not lose it. */
const said = [];

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

// ─────────────────────────────────────────────────────── the conversation

function drawSaid() {
  $('said').innerHTML = said
    .map(
      (one) => `<li data-who="${one.who}">
        <span class="who">${one.who === 'you' ? 'you' : 'agent'}</span>
        <span class="words">${escaped(one.words)}</span>
      </li>`
    )
    .join('');

  const last = $('said').lastElementChild;
  if (last) last.scrollIntoView({ block: 'nearest' });
}

/**
 * Escaped, because half of what appears here is text somebody typed.
 *
 * The agent's replies are built from the catalogue and the diary and are not
 * dangerous; what a visitor types is echoed straight back, and a console that
 * put it into the page as markup would be a small hole in a page anybody can
 * open.
 */
function escaped(text) {
  const box = document.createElement('span');
  box.textContent = String(text ?? '');
  return box.innerHTML;
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

async function startACall() {
  const response = await fetch('/calls', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel: $('channel').value }),
  });

  const reply = await response.json();

  call = reply.call;
  said.length = 0;
  said.push({ who: 'agent', words: reply.reply });
  drawSaid();
  showStage(reply);

  return reply;
}

async function say(words) {
  if (!words.trim()) return;

  if (!call) await startACall();

  said.push({ who: 'you', words });
  drawSaid();

  const response = await fetch(`/calls/${encodeURIComponent(call)}/said`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: words }),
  });

  if (response.status === 404) {
    // An hour of silence and the call is let go of. Starting a new one is the
    // right answer, and saying nothing at all would leave somebody typing into
    // a conversation that has stopped existing.
    said.push({ who: 'agent', words: 'That call has been let go of. Starting a new one.' });
    call = null;
    drawSaid();
    await startACall();
    return;
  }

  const reply = await response.json();

  said.push({ who: 'agent', words: reply.reply });
  drawSaid();
  showStage(reply);

  // Anything the agent does to the clinic shows up on the right, immediately:
  // a slot held during a call stops being offered, and a booking appears.
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
    : `<li class="nothing">Nothing booked yet. Book something on the left.</li>`;
}

// ──────────────────────────────────────────────────────────────── on arrival

await readClinic();
await Promise.all([showCatalogue(), showDiary(), showBookings()]);
await startACall();

$('text').focus();
