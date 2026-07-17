let SpotifyAPI = null;
let activeController = null;
let pendingClick = null;
let activeYt = null;
let QUEUE = null;        // {items: [{btn,row,artist,title}], idx} while playing all
let silentKeeper = null; // near-silent looping <audio>, see startSilentKeeper()

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

function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  try {
    ms.setActionHandler('play', function () {
      if (activeYt) try { activeYt.playVideo(); ms.playbackState = 'playing'; } catch (e) {}
      if (silentKeeper) silentKeeper.play().catch(function () {});
    });
    ms.setActionHandler('pause', function () {
      if (activeYt) try { activeYt.pauseVideo(); ms.playbackState = 'paused'; } catch (e) {}
    });
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
  try { it.row.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
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
}

function toggleQueue() {
  if (QUEUE) { queueStop(); return; }
  if (!(window.YT && YT.Player)) {
    alert('YouTube player API still loading — try again in a second.');
    return;
  }
  const items = queueItems();
  if (!items.length) {
    alert('No YouTube-playable recommendations on this page.');
    return;
  }
  QUEUE = { items: items, idx: -1 };
  startSilentKeeper();
  setupMediaSession();
  document.querySelectorAll('.playall').forEach(function (b) { b.textContent = '⏹ Stop queue'; });
  queuePlay(0);
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
        },
        onStateChange: function (e) {
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
