"""
Optional Discord Rich Presence integration -- shows the current
station and track on your Discord profile while the app is running.

Setup:

  1. pip install pypresence
  2. Make your own free Discord Application (Discord doesn't offer a
     shared app ID for third-party rich presence):
       - https://discord.com/developers/applications -> New Application
       - copy the "Application ID" on the General Information page
  3. Provide that ID either via an environment variable:
       DISCORD_CLIENT_ID=123456789012345678 python main.py
     or by editing DEFAULT_CLIENT_ID below.

If pypresence isn't installed, no client ID is configured, or Discord
just isn't running, this module quietly disables itself -- the rest of
the app works exactly the same either way.
"""

import os
import threading
import time

try:
    from pypresence import Presence
    PYPRESENCE_AVAILABLE = True
except ImportError:
    PYPRESENCE_AVAILABLE = False

DEFAULT_CLIENT_ID = "1528979934981128352"  # <-- paste your own Discord Application ID here, or use the env var

RECONNECT_INTERVAL = 15.0  # seconds between reconnect attempts while Discord isn't found
CLEAR_SENTINEL = object()


class DiscordPresence:
    """Every public method is safe to call whether or not Discord /
    pypresence / a client ID is actually available -- they just no-op
    if not. All Discord I/O happens on a background thread so a
    missing or slow Discord client never blocks the UI thread.
    """

    def __init__(self, client_id=None, on_status=None):
        self.client_id = client_id or os.environ.get("DISCORD_CLIENT_ID", DEFAULT_CLIENT_ID)
        self.available = PYPRESENCE_AVAILABLE and bool(self.client_id)
        self._on_status = on_status or (lambda msg: None)

        self.enabled = self.available  # can be flipped off/on live from a UI checkbox
        self._rpc = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending = None
        self._thread = None

        if self.available:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        elif PYPRESENCE_AVAILABLE and not self.client_id:
            self._on_status(
                "Discord RPC: set DISCORD_CLIENT_ID to enable (see discord_rpc.py)"
            )
        elif not PYPRESENCE_AVAILABLE:
            self._on_status("Discord RPC: pip install pypresence to enable")

    # ---------- public API (safe from any thread) ----------

    def set_enabled(self, value):
        self.enabled = bool(value) and self.available
        if not self.enabled:
            self.clear()

    def update(self, details, state, start_ts=None, large_text=None):
        """Queue a presence update. `details`/`state` are Discord's two
        text lines; `start_ts` (epoch seconds) drives the elapsed timer
        Discord shows next to the activity."""
        if not self.available or not self.enabled:
            return
        with self._lock:
            self._pending = {
                "details": (details or "")[:128] or None,
                "state": (state or "")[:128] or None,
                "start": int(start_ts) if start_ts else None,
                "large_image": "gta5logo",
                "large_text": (large_text or "GTA 5 Radio")[:128],
            }

    def clear(self):
        if not self.available:
            return
        with self._lock:
            self._pending = CLEAR_SENTINEL

    def close(self):
        self.clear()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    # ---------- background thread ----------

    def _run(self):
        while not self._stop.is_set():
            if not self._connected:
                if self._try_connect():
                    self._connected = True
                    self._on_status("Discord Rich Presence connected")
                else:
                    self._stop.wait(RECONNECT_INTERVAL)
                    continue

            payload = None
            with self._lock:
                if self._pending is not None:
                    payload = self._pending
                    self._pending = None

            if payload is CLEAR_SENTINEL:
                self._safe(lambda: self._rpc.clear())
            elif payload:
                self._safe(lambda p=payload: self._rpc.update(
                    **{k: v for k, v in p.items() if v is not None}
                ))

            self._stop.wait(1.0)

        if self._rpc is not None:
            self._safe(lambda: self._rpc.close())

    def _try_connect(self):
        try:
            self._rpc = Presence(self.client_id)
            self._rpc.connect()
            return True
        except Exception:
            self._rpc = None
            return False

    def _safe(self, fn):
        try:
            fn()
        except Exception:
            self._connected = False
            self._on_status("Discord RPC disconnected (will retry)")
