let SpotifyAPI = null;
let activeController = null;
let pendingClick = null;

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

function togglePlayYT(btn) {
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
  const iframe = document.createElement('iframe');
  iframe.className = 'yt';
  iframe.src = 'https://www.youtube.com/embed/' + id + '?autoplay=1&rel=0';
  iframe.allow = 'autoplay; encrypted-media';
  iframe.allowFullscreen = true;
  wrap.appendChild(iframe);
  // Always-visible fallback link — when the uploader has disabled embedding
  // (Error 153), the iframe shows "Watch on YouTube" but it's faster to give
  // the user an explicit, obvious escape hatch right under the player.
  const fallback = document.createElement('a');
  fallback.href = 'https://www.youtube.com/watch?v=' + id;
  fallback.target = '_blank';
  fallback.rel = 'noopener';
  fallback.className = 'yt-fallback';
  fallback.textContent = '↗ Open on YouTube';
  wrap.appendChild(fallback);
  host.appendChild(wrap);
  btn.textContent = '⏸';
}

function togglePlay(btn) {
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
