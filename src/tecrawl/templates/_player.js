let SpotifyAPI = null;
let activeController = null;
let pendingClick = null;
let activeYt = null;
let QUEUE = null;        // {items: [{btn,row,artist,title}], idx} while playing all
let silentKeeper = null; // near-silent looping <audio>, see startSilentKeeper()
let NOW = null;          // {row,title,artist} playing on YouTube, queue or single row

window.onSpotifyIframeApiReady = function (api) {
  SpotifyAPI = api;
  if (pendingClick) {
    const btn = pendingClick;
    pendingClick = null;
    togglePlay(btn);
  }
};

function closeAllEmbeds() {
  if (activeController) {
    try { activeController.destroy(); } catch (e) {}
    activeController = null;
  }
  if (activeYt) {
    try { activeYt.destroy(); } catch (e) {}
    activeYt = null;
  }
  document.querySelectorAll('.embed').forEach(function (e) {
    const host = e.closest('.cand, .seed-head, .pick, .seed-row');
    const otherBtn = host && host.querySelector('.play');
    if (otherBtn) otherBtn.textContent = '▶';
    e.remove();
  });
  NOW = null;
  updateTransport();
}

function toggleSeedList(btn) {
  const pick = btn.closest('.pick');
  const list = pick.querySelector('.seed-list');
  if (!list) return;
  const wasOpen = !list.hidden;
  // Close any other open lists so only one expands at a time.
  document.querySelectorAll('.seed-list').forEach(function (l) {
    if (l !== list) {
      l.hidden = true;
      const otherBadge = l.closest('.pick').querySelector('.hits-badge');
      if (otherBadge) otherBadge.classList.remove('open');
    }
  });
  if (wasOpen) {
    // Collapsing — kill any preview playing inside this list so audio
    // doesn't keep going while the row is hidden.
    if (list.querySelector('.embed')) closeAllEmbeds();
  }
  list.hidden = wasOpen;
  btn.classList.toggle('open', !wasOpen);
}

// YouTube error codes → human-readable reason (shown under the player so a
// dead embed is diagnosable instead of YouTube's opaque "An error occurred").
const YT_ERRORS = {
  2: 'bad video id',
  5: 'player error in this browser',
  100: 'video removed or private',
  101: 'embedding disabled by the uploader',
  150: 'embedding disabled by the uploader',
};

// --- Play-all queue with media-key control (YouTube-only) ---
// Every .pick/.cand row with a YouTube id, in page order (top picks first),
// deduped. The browser's Media Session API maps the keyboard's
// play/pause/next/previous media keys onto the queue, so skipping works
// with the page in the background.

function silentWavUrl() {
  // 0.1 s of 8 kHz 16-bit mono silence, built at runtime.
  const n = 800, size = 44 + n * 2;
  const buf = new ArrayBuffer(size), v = new DataView(buf);
  const w = function (o, s) {
    for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
  };
  w(0, 'RIFF'); v.setUint32(4, size - 8, true); w(8, 'WAVEfmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 8000, true); v.setUint32(28, 16000, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

function startSilentKeeper() {
  // The OS routes media keys to whichever frame audibly plays. The YouTube
  // IFRAME would win and swallow next/previous. A near-silent loop makes
  // THIS page an audible player too, so our Media Session handlers (with
  // queue metadata) take the keys. Started from the click gesture.
  if (!silentKeeper) {
    silentKeeper = new Audio(silentWavUrl());
    silentKeeper.loop = true;
    silentKeeper.volume = 0.001;
  }
  silentKeeper.play().catch(function () {});
}

function stopSilentKeeper() {
  if (silentKeeper) silentKeeper.pause();
}

function queueItems() {
  const items = [];
  const seen = new Set();
  document.querySelectorAll('.pick, .cand').forEach(function (row) {
    const btn = row.querySelector('.play.yt[data-yt-id]');
    if (!btn) return;
    const id = btn.dataset.ytId;
    if (seen.has(id)) return;
    seen.add(id);
    const t = row.querySelector('.pick-title, .cand-title');
    const a = row.querySelector('.pick-artist, .cand-artist');
    items.push({
      btn: btn,
      row: row,
      title: (t ? t.textContent : '').trim(),
      artist: (a ? a.textContent : '').trim(),
    });
  });
  return items;
}

// Play/pause/next/previous live in one place so the media keys and the
// transport bar's buttons can't drift apart — and so the buttons are a way
// to exercise the key handlers when the keys themselves are misbehaving.
function ytPlaying() {
  try {
    return !!activeYt && !!activeYt.getPlayerState && activeYt.getPlayerState() === 1;
  } catch (e) { return false; }
}

function playCurrent() {
  if (activeYt) { try { activeYt.playVideo(); } catch (e) {} }
  if (silentKeeper) silentKeeper.play().catch(function () {});
  if ('mediaSession' in navigator) {
    try { navigator.mediaSession.playbackState = 'playing'; } catch (e) {}
  }
  updateTransport();
}

function pauseCurrent() {
  // The silent keeper deliberately keeps running: it's what holds the OS
  // media session, so pausing it would hand the keys back to the iframe.
  if (activeYt) { try { activeYt.pauseVideo(); } catch (e) {} }
  if ('mediaSession' in navigator) {
    try { navigator.mediaSession.playbackState = 'paused'; } catch (e) {}
  }
  updateTransport();
}

function toggleCurrent() {
  if (ytPlaying()) pauseCurrent(); else playCurrent();
}

function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  try {
    ms.setActionHandler('play', playCurrent);
    ms.setActionHandler('pause', pauseCurrent);
    ms.setActionHandler('nexttrack', function () { if (QUEUE) queuePlay(QUEUE.idx + 1); });
    ms.setActionHandler('previoustrack', function () { if (QUEUE) queuePlay(QUEUE.idx - 1); });
  } catch (e) {}
}

function queuePlay(i) {
  if (!QUEUE) return;
  if (i < 0) i = 0;
  if (i >= QUEUE.items.length) { queueStop(); return; }
  QUEUE.idx = i;
  const it = QUEUE.items[i];
  document.querySelectorAll('.playing').forEach(function (r) { r.classList.remove('playing'); });
  it.row.classList.add('playing');
  // Once you've scrolled away deliberately, stop yanking the page around on
  // every advance — the transport bar keeps the playing track reachable, and
  // its title is a click-to-jump back.
  if (!userScrolled) {
    try { it.row.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
  }
  closeAllEmbeds();
  togglePlayYT(it.btn, true);
  if ('mediaSession' in navigator) {
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: it.title, artist: it.artist, album: 'teCrawl',
      });
      navigator.mediaSession.playbackState = 'playing';
    } catch (e) {}
  }
}

function queueStop() {
  if (!QUEUE) return;
  QUEUE = null;
  closeAllEmbeds();
  stopSilentKeeper();
  document.querySelectorAll('.playing').forEach(function (r) { r.classList.remove('playing'); });
  if ('mediaSession' in navigator) {
    try { navigator.mediaSession.playbackState = 'none'; } catch (e) {}
  }
  document.querySelectorAll('.playall').forEach(function (b) { b.textContent = '▶ Play all'; });
  document.querySelectorAll('.playall-mini').forEach(function (b) { b.textContent = '▶'; });
}

// --- Fixed transport bar (bottom of the viewport) ---
// Whatever is playing on YouTube gets an always-visible strip: previous /
// play-pause / next / stop, the track name, queue position, and a scrubbable
// progress bar. Prev and next go through queuePlay(), the same entry point the
// media keys use, so skipping never depends on the keys working. Built here
// rather than in the page templates so the run page and the search page (whose
// results arrive over SSE) share one copy.

let BAR = null;
let progressTimer = null;
let userScrolled = false; // set by real user scrolling, see queuePlay()

['wheel', 'touchmove'].forEach(function (ev) {
  window.addEventListener(ev, function () { userScrolled = true; }, { passive: true });
});
window.addEventListener('keydown', function (e) {
  // Typing in a feedback note isn't scrolling.
  const tag = e.target && e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  const scrollKeys = ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '];
  if (scrollKeys.indexOf(e.key) !== -1) userScrolled = true;
});

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m + ':' + (sec < 10 ? '0' : '') + sec;
}

// Title/artist for a playing row, whichever kind of row it is.
function rowMeta(row) {
  const t = row && row.querySelector('.cand-title, .pick-title, .seed-title');
  const a = row && row.querySelector('.cand-artist, .pick-artist, .seed-artist');
  return {
    title: (t ? t.textContent : '').trim(),
    artist: (a ? a.textContent : '').trim(),
  };
}

function ensureBar() {
  if (BAR) return BAR;
  const el = document.createElement('div');
  el.className = 'transport';
  el.hidden = true;
  el.innerHTML =
    '<div class="tp-rail" title="Seek"><div class="tp-fill"></div></div>' +
    '<div class="tp-body">' +
      '<div class="tp-controls">' +
        '<button class="tp-btn tp-prev" title="Previous track">⏮</button>' +
        '<button class="tp-btn tp-toggle" title="Play / pause">⏸</button>' +
        '<button class="tp-btn tp-next" title="Next track">⏭</button>' +
        '<button class="tp-btn tp-stop" title="Stop">⏹</button>' +
      '</div>' +
      '<button class="tp-track" title="Scroll to the playing row">' +
        '<span class="tp-title"></span><span class="tp-artist"></span>' +
      '</button>' +
      '<span class="tp-pos"></span>' +
      '<span class="tp-time">0:00 / 0:00</span>' +
    '</div>';
  document.body.appendChild(el);
  BAR = {
    el: el,
    rail: el.querySelector('.tp-rail'),
    fill: el.querySelector('.tp-fill'),
    prev: el.querySelector('.tp-prev'),
    toggle: el.querySelector('.tp-toggle'),
    next: el.querySelector('.tp-next'),
    stop: el.querySelector('.tp-stop'),
    track: el.querySelector('.tp-track'),
    title: el.querySelector('.tp-title'),
    artist: el.querySelector('.tp-artist'),
    pos: el.querySelector('.tp-pos'),
    time: el.querySelector('.tp-time'),
  };
  BAR.prev.onclick = function () { if (QUEUE) queuePlay(QUEUE.idx - 1); };
  BAR.next.onclick = function () { if (QUEUE) queuePlay(QUEUE.idx + 1); };
  BAR.toggle.onclick = toggleCurrent;
  BAR.stop.onclick = function () { if (QUEUE) queueStop(); else closeAllEmbeds(); };
  BAR.track.onclick = function () {
    if (!NOW) return;
    userScrolled = false; // following along again
    try { NOW.row.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
  };
  BAR.rail.onclick = function (e) {
    if (!activeYt || !activeYt.seekTo) return;
    let dur = 0;
    try { dur = activeYt.getDuration() || 0; } catch (err) {}
    if (!dur) return;
    const box = BAR.rail.getBoundingClientRect();
    const pct = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    try { activeYt.seekTo(pct * dur, true); } catch (err) {}
    tickProgress();
  };
  return BAR;
}

function updateTransport() {
  const bar = ensureBar();
  if (!NOW) {
    stopProgress();
    bar.el.hidden = true;
    document.body.classList.remove('with-transport');
    return;
  }
  bar.el.hidden = false;
  document.body.classList.add('with-transport');
  bar.title.textContent = NOW.title || 'Playing';
  bar.artist.textContent = NOW.artist || '';
  const inQueue = !!QUEUE;
  bar.prev.disabled = !inQueue;
  bar.next.disabled = !inQueue;
  bar.prev.title = inQueue ? 'Previous track' : 'Previous — only while a queue is playing';
  bar.next.title = inQueue ? 'Next track' : 'Next — only while a queue is playing';
  bar.stop.title = inQueue ? 'Stop the queue' : 'Stop';
  bar.pos.textContent = inQueue ? (QUEUE.idx + 1) + ' / ' + QUEUE.items.length : '';
  bar.toggle.textContent = ytPlaying() ? '⏸' : '▶';
  // The plain-iframe fallback exposes no timing, so it degrades to controls.
  bar.el.classList.toggle('no-progress', !!NOW.noProgress);
}

function startProgress() {
  stopProgress();
  progressTimer = setInterval(tickProgress, 500);
  tickProgress();
}

function stopProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  if (BAR) {
    BAR.fill.style.width = '0%';
    BAR.time.textContent = '0:00 / 0:00';
  }
}

function tickProgress() {
  if (!BAR || !NOW || !activeYt) return;
  let cur = 0, dur = 0;
  try {
    cur = activeYt.getCurrentTime() || 0;
    dur = activeYt.getDuration() || 0;
  } catch (e) { return; } // player mid-destroy
  BAR.fill.style.width = dur ? Math.min(100, (cur / dur) * 100) + '%' : '0%';
  BAR.time.textContent = fmtTime(cur) + ' / ' + fmtTime(dur);
  BAR.toggle.textContent = ytPlaying() ? '⏸' : '▶';
  // Gives the OS media UI a real scrubber too.
  if ('mediaSession' in navigator && navigator.mediaSession.setPositionState && dur) {
    try {
      navigator.mediaSession.setPositionState({
        duration: dur, position: Math.min(cur, dur), playbackRate: 1,
      });
    } catch (e) {}
  }
}

// Rows belonging to the section a play-all button lives in — a group-head's
// section is the .group div right after it; the ★ Top picks button's
// section is every .pick in its enclosing .top-picks block. Global header
// buttons (fromEl omitted) have no section, so the queue starts at index 0.
function sectionRows(fromEl) {
  if (!fromEl) return null;
  const groupHead = fromEl.closest('.group-head');
  if (groupHead && groupHead.nextElementSibling && groupHead.nextElementSibling.classList.contains('group')) {
    return groupHead.nextElementSibling.querySelectorAll('.cand');
  }
  const topPicks = fromEl.closest('.top-picks');
  if (topPicks) return topPicks.querySelectorAll('.pick');
  return null;
}

function toggleQueue(fromEl) {
  const origin = fromEl || null;
  if (QUEUE && QUEUE.originEl === origin) { queueStop(); return; }
  if (!(window.YT && YT.Player)) {
    alert('YouTube player API still loading — try again in a second.');
    return;
  }
  const items = queueItems();
  if (!items.length) {
    alert('No YouTube-playable recommendations on this page.');
    return;
  }
  let startIdx = 0;
  const rows = sectionRows(fromEl);
  if (rows && rows.length) {
    for (let i = 0; i < items.length; i++) {
      if (Array.prototype.indexOf.call(rows, items[i].row) !== -1) { startIdx = i; break; }
    }
  }
  QUEUE = { items: items, idx: -1, originEl: origin };
  userScrolled = false;
  startSilentKeeper();
  setupMediaSession();
  document.querySelectorAll('.playall').forEach(function (b) { b.textContent = '▶ Play all'; });
  document.querySelectorAll('.playall-mini').forEach(function (b) { b.textContent = '▶'; });
  if (origin) origin.textContent = '⏹';
  else document.querySelectorAll('.playall').forEach(function (b) { b.textContent = '⏹ Stop queue'; });
  queuePlay(startIdx);
}

function togglePlayYT(btn, fromQueue) {
  if (!fromQueue && QUEUE) queueStop(); // manual click takes over from the queue
  const host = btn.closest('.cand, .seed-head, .pick, .seed-row');
  const existing = host.querySelector('.embed');
  if (existing) {
    closeAllEmbeds();
    return;
  }
  closeAllEmbeds();
  const id = btn.dataset.ytId;
  const wrap = document.createElement('div');
  wrap.className = 'embed';
  const target = document.createElement('div');
  wrap.appendChild(target);
  // Always-visible fallback link — when the embed can't play (embedding
  // disabled, stream blocked, …) this is the obvious escape hatch.
  const fallback = document.createElement('a');
  fallback.href = 'https://www.youtube.com/watch?v=' + id;
  fallback.target = '_blank';
  fallback.rel = 'noopener';
  fallback.className = 'yt-fallback';
  fallback.textContent = '↗ Open on YouTube';
  wrap.appendChild(fallback);
  host.appendChild(wrap);
  btn.textContent = '⏸';
  NOW = Object.assign({ row: host, noProgress: false }, rowMeta(host));
  updateTransport();

  if (window.YT && YT.Player) {
    // IFrame API path: created inside the click gesture so autoplay is
    // allowed; play again on 'ready' as belt-and-braces. The nocookie host
    // avoids the cookie/consent-related "An error occurred" failures.
    activeYt = new YT.Player(target, {
      host: 'https://www.youtube-nocookie.com',
      width: '100%',
      videoId: id,
      playerVars: { autoplay: 1, playsinline: 1, rel: 0 },
      events: {
        onReady: function (e) {
          try { e.target.playVideo(); } catch (err) {}
          startProgress();
          updateTransport();
        },
        onStateChange: function (e) {
          updateTransport();
          if (QUEUE && e.data === 0) queuePlay(QUEUE.idx + 1); // 0 = ended
        },
        onError: function (e) {
          const why = YT_ERRORS[e.data] || ('error ' + e.data);
          const note = document.createElement('div');
          note.className = 'yt-error';
          note.textContent = 'Embed failed: ' + why + ' — use the YouTube link below.';
          wrap.insertBefore(note, fallback);
          if (QUEUE) {
            // Skip dead embeds so the queue keeps moving.
            setTimeout(function () { if (QUEUE) queuePlay(QUEUE.idx + 1); }, 1200);
          }
        },
      },
    });
    const iframe = activeYt.getIframe && activeYt.getIframe();
    if (iframe) iframe.classList.add('yt');
  } else {
    // Plain-iframe fallback when the API script hasn't loaded (first click
    // on a cold page, or the script is blocked by an extension).
    const iframe = document.createElement('iframe');
    iframe.className = 'yt';
    iframe.src = 'https://www.youtube-nocookie.com/embed/' + id
      + '?autoplay=1&playsinline=1&rel=0';
    iframe.allow = 'autoplay; encrypted-media';
    iframe.allowFullscreen = true;
    wrap.replaceChild(iframe, target);
    if (NOW) NOW.noProgress = true;
    updateTransport();
  }
}

// --- 👍/👎 feedback (record-only: appends to feedback.jsonl, no ranking
// effects yet) + per-row "why?" explanations. State is applied client-side
// from /api/feedback so even old archived pages show current verdicts. ---
let FEEDBACK = null;

function fbKey(artist, title) {
  return (artist + '||' + title).toLowerCase();
}

function paintFb(span) {
  const k = fbKey(span.dataset.artist, span.dataset.title);
  const v = FEEDBACK ? FEEDBACK[k] : undefined;
  span.querySelector('.fb-up').classList.toggle('on', v === 'up');
  span.querySelector('.fb-down').classList.toggle('on', v === 'down');
}

function applyFeedbackStates(root) {
  if (!FEEDBACK) return;
  (root || document).querySelectorAll('.fb').forEach(paintFb);
}

fetch('/api/feedback')
  .then(function (r) { return r.json(); })
  .then(function (map) { FEEDBACK = map || {}; applyFeedbackStates(); })
  .catch(function () { FEEDBACK = {}; });

function fbPayload(span, verdict, comment) {
  const d = span.dataset;
  const p = {
    verdict: verdict,
    artist: d.artist,
    title: d.title,
    source: d.source || '',
    source_detail: d.detail || '',
    seed: d.seed || '',
    context: d.context || '',
    page: location.pathname,
  };
  if (comment) p.comment = comment;
  return p;
}

function postFb(payload) {
  return fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(function (r) {
    if (!r.ok) throw new Error('bad status');
  });
}

function closeFbNote(row) {
  const open = row.querySelector('.fb-note');
  if (open) open.remove();
}

// After a thumb is set, offer an optional note explaining the verdict —
// the user's own reason is the highest-signal data for tuning the
// algorithm later. Chips save in one tap; free text saves on Enter/Save.
function openFbNote(span, verdict) {
  const row = span.closest('.cand, .pick');
  closeFbNote(row);
  const panel = document.createElement('div');
  panel.className = 'fb-note';

  const input = document.createElement('input');
  input.type = 'text';
  input.maxLength = 300;
  input.placeholder = verdict === 'down'
    ? "optional: why is it bad? (helps tune the algorithm)"
    : "optional: why is it good? (helps tune the algorithm)";

  function saveNote(text) {
    const t = (text || '').trim();
    if (!t) { panel.remove(); return; }
    postFb(fbPayload(span, verdict, t)).then(function () {
      panel.innerHTML = '';
      const ok = document.createElement('span');
      ok.className = 'done';
      ok.textContent = '✓ noted: ' + t;
      panel.appendChild(ok);
      setTimeout(function () { panel.remove(); }, 1500);
    }).catch(function () {
      alert('Saving the note failed — is the tecrawl server running?');
    });
  }

  const chips = verdict === 'down'
    ? ['totally irrelevant', 'wrong vibe', 'already know it']
    : ['great find', 'more like this'];
  chips.forEach(function (label) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.textContent = label;
    chip.onclick = function () { saveNote(label); };
    panel.appendChild(chip);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') saveNote(input.value);
    if (e.key === 'Escape') panel.remove();
  });
  panel.appendChild(input);

  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'save';
  save.textContent = 'Save note';
  save.onclick = function () { saveNote(input.value); };
  panel.appendChild(save);

  row.appendChild(panel);
  input.focus();
}

function sendFeedback(btn, verdict) {
  const span = btn.closest('.fb');
  const d = span.dataset;
  const k = fbKey(d.artist, d.title);
  const current = FEEDBACK && FEEDBACK[k];
  const v = current === verdict ? 'clear' : verdict; // same thumb again = undo
  postFb(fbPayload(span, v)).then(function () {
    if (!FEEDBACK) FEEDBACK = {};
    if (v === 'clear') delete FEEDBACK[k]; else FEEDBACK[k] = v;
    paintFb(span);
    const row = span.closest('.cand, .pick');
    if (v === 'clear') closeFbNote(row);
    else openFbNote(span, v);
  }).catch(function () {
    alert('Saving feedback failed — is the tecrawl server running?');
  });
}

function toggleWhy(btn) {
  const row = btn.closest('.cand, .pick');
  const existing = row.querySelector('.why-panel');
  if (existing) { existing.remove(); return; }
  const div = document.createElement('div');
  div.className = 'why-panel';
  div.textContent = btn.dataset.why;
  row.appendChild(div);
}

// --- "Show more" within a section: POST /api/more, append the returned rows
// into this section's .group. Needs the tecrawl server running (a page opened
// straight off disk just gets a "server running?" note). The section's Discogs
// identifiers ride along on the button's data-* attributes; the already-shown
// (artist,title) pairs are read from the DOM so a click never re-adds a row. ---
function showMore(btn) {
  if (btn.dataset.loading) return;
  const group = btn.previousElementSibling; // the .group this button follows
  if (!group || !group.classList.contains('group')) return;
  const shown = [];
  group.querySelectorAll('.cand').forEach(function (row) {
    const t = row.querySelector('.cand-title');
    const a = row.querySelector('.cand-artist');
    if (t && a) shown.push([a.textContent.trim(), t.textContent.trim()]);
  });
  const orig = btn.dataset.orig || btn.textContent;
  btn.dataset.orig = orig;
  btn.dataset.loading = '1';
  btn.disabled = true;
  btn.textContent = '… loading';
  const body = {
    source: btn.dataset.source,
    artist_id: btn.dataset.artistId || '',
    label_id: btn.dataset.labelId || '',
    release_id: btn.dataset.releaseId || '',
    styles: btn.dataset.styles || '',
    label: btn.dataset.label || '',
    seed_artist: btn.dataset.seedArtist || '',
    seed_title: btn.dataset.seedTitle || '',
    page: parseInt(btn.dataset.page || '1', 10),
    batch: parseInt(btn.dataset.batch || '10', 10),
    shown: shown,
  };
  fetch('/api/more', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(function (r) {
    if (!r.ok) throw new Error('bad status');
    return r.json();
  }).then(function (d) {
    if (d.error) throw new Error(d.error);
    if (d.rows) {
      group.insertAdjacentHTML('beforeend', d.rows);
      applyFeedbackStates(group);
    }
    btn.dataset.page = String(d.next_page || (parseInt(btn.dataset.page || '1', 10) + 1));
    delete btn.dataset.loading;
    if (d.exhausted || !d.count) {
      btn.textContent = d.count ? 'No more to load' : 'Nothing more found';
      btn.disabled = true; // stays disabled — the source is tapped out
    } else {
      btn.textContent = orig;
      btn.disabled = false;
    }
  }).catch(function () {
    delete btn.dataset.loading;
    btn.textContent = 'Load failed — is the server running?';
    setTimeout(function () { btn.textContent = orig; btn.disabled = false; }, 2500);
  });
}

function togglePlay(btn) {
  if (QUEUE) queueStop(); // manual Spotify play takes over from the queue
  const cand = btn.closest('.cand, .seed-head, .pick, .seed-row');
  const existing = cand.querySelector('.embed');
  if (existing) {
    closeAllEmbeds();
    return;
  }
  // If the API isn't ready yet, defer this click and run when it loads.
  if (!SpotifyAPI) {
    pendingClick = btn;
    btn.textContent = '…';
    return;
  }
  closeAllEmbeds();

  const wrap = document.createElement('div');
  wrap.className = 'embed';
  const placeholder = document.createElement('div');
  wrap.appendChild(placeholder);
  cand.appendChild(wrap);
  btn.textContent = '⏸';

  const kind = btn.dataset.spotifyType || 'track';
  const uri = 'spotify:' + kind + ':' + btn.dataset.spotifyId;
  SpotifyAPI.createController(
    placeholder,
    {
      width: '100%',
      height: kind === 'album' ? 152 : 80,
    },
    function (controller) {
      activeController = controller;
      let playedOnce = false;
      // Two-stage init: create the player empty, then on 'ready' load the
      // URI and immediately call play(). loadUri+play in the same tick is
      // the pattern that survives browser autoplay policy because both
      // calls happen synchronously after the user-initiated 'ready' event.
      controller.addListener('ready', function () {
        if (playedOnce) return;
        playedOnce = true;
        controller.loadUri(uri);
        controller.play();
      });
      // Fallback if 'ready' fired before our listener attached (race
      // condition on cold start): try once after a short delay.
      setTimeout(function () {
        if (!playedOnce) {
          playedOnce = true;
          try { controller.loadUri(uri); controller.play(); } catch (e) {}
        }
      }, 800);
    }
  );
}
