"""Spotify user OAuth + Connect playback control.

Separate from spotify.py on purpose. That module uses the *client
credentials* flow, which authenticates the application and can only read
public catalog data (search, track lookup). Controlling playback needs the
*authorization code* flow, which authenticates the human and yields a token
tied to their account and Premium subscription.

Why Connect rather than the embed player: the Spotify IFrame embed decides
between a 30-second preview and the full track based on whether it can see
a logged-in Premium session, and that session lives in a third-party cookie
which browsers block by default. Connect sidesteps the browser entirely and
tells Spotify's own client (desktop app, phone, speaker) what to play, so a
Premium user gets the full track through their real player.

The refresh token stored here grants playback control of the user's account.
It lives in .spotify_token.json at the project root, gitignored, mode 0600.
"""

import json
import os
import secrets
import threading
import time

import requests

from . import config

TOKEN_PATH = config.PROJECT_ROOT / ".spotify_token.json"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# Read what's playing and on which devices; change what's playing. No
# playlist or library scopes — this feature only drives the player.
SCOPES = "user-read-playback-state user-modify-playback-state"

# Must match a Redirect URI registered on the Spotify app exactly. Spotify
# rejects the hostname "localhost", so this is the loopback IP literal.
_PORT = [8765]
_LOCK = threading.RLock()
_STATE: dict = {}


class NotConnected(Exception):
    """No user token yet (or it was revoked). The UI offers a login link."""


class NoActiveDevice(Exception):
    """Authorized, but Spotify has no device to play on right now."""


class ConnectError(Exception):
    """Any other playback failure, with Spotify's own message where useful."""


def set_port(port: int) -> None:
    """The redirect URI embeds the server port, so serve() tells us the real
    one. A non-default port needs its own Redirect URI on the Spotify app."""
    _PORT[0] = port


def redirect_uri() -> str:
    return f"http://127.0.0.1:{_PORT[0]}/callback"


# --- token storage -------------------------------------------------------


def _load() -> dict:
    with _LOCK:
        try:
            return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def _save(data: dict) -> None:
    with _LOCK:
        TOKEN_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Holds a refresh token: keep it owner-only.
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except OSError:
            pass


def is_connected() -> bool:
    return bool(_load().get("refresh_token"))


def disconnect() -> None:
    """Forget the user token. Spotify-side access is revoked from the user's
    account page (Apps), which we can't do for them over the API."""
    with _LOCK:
        try:
            TOKEN_PATH.unlink()
        except OSError:
            pass


# --- authorization code flow --------------------------------------------


def authorize_url() -> str:
    """Consent URL. The state is a CSRF nonce checked on the way back."""
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        raise NotConnected("Spotify credentials missing from .env")
    state = secrets.token_urlsafe(16)
    _STATE["state"] = state
    from urllib.parse import urlencode

    return AUTH_URL + "?" + urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "scope": SCOPES,
        "state": state,
        # Always re-prompt, so switching accounts doesn't silently reuse the
        # previous one — easy to hit while testing.
        "show_dialog": "true",
    })


def exchange_code(code: str, state: str) -> None:
    """Swap the one-time code for access + refresh tokens."""
    expected = _STATE.pop("state", None)
    if not expected or state != expected:
        raise ConnectError("OAuth state mismatch — start the login again")
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
        },
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
        timeout=15,
    )
    if r.status_code != 200:
        raise ConnectError(_error_text(r, "token exchange failed"))
    payload = r.json()
    _save({
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": time.time() + payload.get("expires_in", 3600),
        "scope": payload.get("scope", ""),
    })


def _refresh(data: dict) -> dict:
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": data["refresh_token"]},
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
        timeout=15,
    )
    if r.status_code in (400, 401):
        # Revoked from the user's Spotify account page, or the app was
        # replaced. Drop it so the UI shows "connect" instead of erroring
        # on every click.
        disconnect()
        raise NotConnected("Spotify access was revoked — connect again")
    if r.status_code != 200:
        raise ConnectError(_error_text(r, "token refresh failed"))
    payload = r.json()
    data["access_token"] = payload["access_token"]
    data["expires_at"] = time.time() + payload.get("expires_in", 3600)
    # A refresh response usually omits refresh_token; keep the existing one.
    if payload.get("refresh_token"):
        data["refresh_token"] = payload["refresh_token"]
    _save(data)
    return data


def access_token() -> str:
    data = _load()
    if not data.get("refresh_token"):
        raise NotConnected("Not connected to Spotify")
    if data.get("expires_at", 0) < time.time() + 30:
        data = _refresh(data)
    return data["access_token"]


# --- playback ------------------------------------------------------------


def _error_text(r: requests.Response, fallback: str) -> str:
    try:
        body = r.json()
        msg = body.get("error", {})
        if isinstance(msg, dict):
            return msg.get("message") or fallback
        return body.get("error_description") or str(msg) or fallback
    except ValueError:
        return fallback


def _api(method: str, path: str, **kw) -> dict | None:
    token = access_token()
    r = requests.request(
        method,
        API + path,
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": config.USER_AGENT},
        timeout=15,
        **kw,
    )
    if r.status_code == 401:
        raise NotConnected("Spotify rejected the token — connect again")
    if r.status_code == 403:
        # The usual cause: the account is not Premium. Connect playback
        # control is Premium-only, and Spotify says so in the body.
        raise ConnectError(_error_text(r, "Spotify refused (Premium required?)"))
    if r.status_code == 404:
        raise NoActiveDevice("No active Spotify device")
    if r.status_code == 429:
        raise ConnectError("Spotify rate-limited the player — try again shortly")
    if r.status_code >= 400:
        raise ConnectError(_error_text(r, f"Spotify error {r.status_code}"))
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def devices() -> list[dict]:
    data = _api("GET", "/me/player/devices") or {}
    return data.get("devices", [])


def now_playing() -> dict | None:
    """Current player state, or None when Spotify is idle (204)."""
    return _api("GET", "/me/player")


def play(spotify_id: str, kind: str = "track", device_id: str | None = None) -> None:
    """Start a track or album on the user's active (or chosen) device."""
    uri = f"spotify:{kind}:{spotify_id}"
    body = {"context_uri": uri} if kind == "album" else {"uris": [uri]}
    params = {"device_id": device_id} if device_id else None
    try:
        _api("PUT", "/me/player/play", json=body, params=params)
    except NoActiveDevice:
        # 404 here means "no device to play on". If exactly one device is
        # known but idle, wake it rather than making the user go find it.
        available = devices()
        if len(available) == 1:
            _api("PUT", "/me/player/play",
                 json=body, params={"device_id": available[0]["id"]})
            return
        raise


def pause() -> None:
    _api("PUT", "/me/player/pause")


def next_track() -> None:
    _api("POST", "/me/player/next")


def previous_track() -> None:
    _api("POST", "/me/player/previous")


def status() -> dict:
    """One call the UI can poll: is playback control available, and where."""
    if not is_connected():
        return {"connected": False, "devices": [], "active": None}
    try:
        devs = devices()
    except NotConnected:
        return {"connected": False, "devices": [], "active": None}
    except (ConnectError, NoActiveDevice, requests.RequestException) as e:
        return {"connected": True, "devices": [], "active": None, "error": str(e)}
    active = next((d["id"] for d in devs if d.get("is_active")), None)
    return {
        "connected": True,
        "devices": [{"id": d["id"], "name": d["name"], "type": d.get("type", ""),
                     "active": bool(d.get("is_active"))} for d in devs],
        "active": active,
    }
