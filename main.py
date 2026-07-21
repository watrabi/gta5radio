"""
Desktop radio app modeled on GTA V's radio: circular station wheel,
random-with-no-repeats song rotation, DJ/ad/news interjections, a
short crossfade when you switch stations (mimicking the game's audio
dip when the wheel opens), a live progress bar for the current clip,
and optional Discord Rich Presence.

Every station is treated as continuously broadcasting from the moment
the app launches, whether you're listening to it or not. Rather than
literally decoding audio for every station at once in the background
(wasteful, and fragile across audio formats), each station's sequence
is deterministic -- same seed always produces the same order of clips
-- so when you tune in, the app fast-forwards a fresh sequencer from
t=0 up to "now" using each clip's duration, and starts playback right
at the point a real always-on station would be at. Tuning back to a
station you left earlier picks up further along, not back at the start.

Starting a clip mid-way through normally means pygame.mixer.music's
`start=` offset -- reliable for mp3/ogg, but SDL_mixer's WAV handling
frequently ignores that offset. For WAV specifically, audio_backend.py
slices the raw PCM data with the stdlib `wave` module and plays the
trimmed clip through a dedicated channel instead, so tuning into a
station mid-song works regardless of format.

Run with:  python main.py

See README.md for how to populate the stations/ folder with your own
extracted audio, and discord_rpc.py for enabling Discord Rich Presence.
"""

import hashlib
import queue
import threading
import time
import tkinter as tk
from pathlib import Path

import pygame

from audio_backend import MainPlayer
from discord_rpc import DiscordPresence
from gui_widgets import HoverButton, ProgressBar, fmt_time
from stations import load_stations
from player import Sequencer
from wheel import RadioWheel

BASE_DIR = Path(__file__).resolve().parent
STATIONS_DIR = BASE_DIR / "stations"

# ---------- palette ----------
BG = "#0d0d0d"
PANEL = "#161616"
PANEL_BORDER = "#282828"
ACCENT = "#e63946"
ACCENT_DIM = "#7a1620"
TEXT = "#eaeaea"
MUTED = "#888888"
TROUGH = "#2a2a2a"

FONT_FAMILY = "Helvetica Neue"

SWITCH_FADE_MS = 350  # how long the crossfade dip lasts when changing stations
DUCK_FACTOR = 0.25    # how quiet the music gets while a DJ overlay is talking over it
PROGRESS_POLL_MS = 200

# Rough clip-length guesses used only when we can't read a file's real
# duration (e.g. an exotic format Sound() can't open for length probing).
DEFAULT_DURATIONS = {"song": 180.0, "dj": 6.0, "ad": 20.0, "news": 25.0}

ICONS = {"song": "\U0001F3B5", "dj": "\U0001F399", "ad": "\U0001F4E2", "news": "\U0001F4F0"}
KIND_LABELS = {"song": "Now Playing", "dj": "DJ", "ad": "Advertisement", "news": "News"}
KIND_COLORS = {"song": ACCENT, "dj": "#4ea8de", "ad": "#f2b134", "news": "#8ac926"}

_duration_cache = {}


def get_duration(path: Path, fallback: float) -> float:
    """Cached lookup of a clip's length in seconds, for the background
    timeline simulation and the progress bar. Falls back to a rough
    guess if the format can't be probed."""
    key = str(path)
    if key in _duration_cache:
        return _duration_cache[key]
    length = fallback
    try:
        probed = pygame.mixer.Sound(key).get_length()
        if probed and probed > 0:
            length = probed
    except pygame.error:
        pass
    _duration_cache[key] = length
    return length


class RadioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Radio")
        self.geometry("460x800")
        self.configure(bg=BG)
        self.resizable(False, False)

        pygame.mixer.init()
        pygame.mixer.set_reserved(1)  # keep channel 0 free for MainPlayer's wav-slice path
        self.player = MainPlayer()

        self.stations = load_stations(STATIONS_DIR)
        self.current_station = None
        self.sequencer = None
        self.playing = False
        self.volume = 0.7

        # Shared anchor for the "always running" simulation -- every
        # station's timeline is computed relative to this moment.
        self._session_start = time.time()

        self._stop_flag = threading.Event()
        self._play_thread = None
        self._switch_lock = threading.Lock()
        self._intro_channel = None  # the pygame Channel currently playing a DJ overlay, if any
        self._primed_clip = None    # a clip pre-computed by _simulate_to_now, played first on switch
        self._primed_offset = 0.0   # how far into that clip we should start

        # Progress-bar state, written by the playback thread, read by the UI poll.
        self._clip_duration = 0.0
        self._clip_kind = None
        self._progress_active = False

        # Background threads never touch widgets directly -- Tkinter isn't
        # thread-safe and doing so can crash the app outright (especially
        # on macOS). They push callables here instead, and only the main
        # thread drains the queue and runs them.
        self._ui_queue = queue.Queue()
        self.after(50, self._poll_ui_queue)
        self.after(PROGRESS_POLL_MS, self._poll_progress)

        self.discord = DiscordPresence(on_status=self._discord_status)

        self._build_ui()

        if not self.stations:
            self.now_playing_var.set("No stations found")
            self.status_var.set("Add folders under stations/<name>/songs (see README.md), then restart.")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------

    def _build_ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(pady=(22, 6))
        tk.Label(header, text="RADIO", font=(FONT_FAMILY, 27, "bold"),
                  fg=TEXT, bg=BG).pack()
        underline = tk.Canvas(header, width=54, height=3, bg=BG, highlightthickness=0)
        underline.create_rectangle(0, 0, 54, 3, fill=ACCENT, width=0)
        underline.pack(pady=(4, 0))

        self.wheel = RadioWheel(
            self, self.stations, size=330,
            on_select=self._on_wheel_select,
            on_center_click=self.toggle_play,
        )
        self.wheel.pack(pady=(14, 16))
        self.wheel.set_center_label("SELECT\nA STATION")

        # ----- now-playing panel -----
        panel = tk.Frame(self, bg=PANEL, highlightbackground=PANEL_BORDER,
                          highlightthickness=1)
        panel.pack(padx=24, pady=(0, 14), fill="x")
        inner = tk.Frame(panel, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=14)

        self.kind_var = tk.StringVar(value="Click a wedge to tune in")
        self.kind_label = tk.Label(inner, textvariable=self.kind_var,
                                    font=(FONT_FAMILY, 10, "bold"), fg=MUTED, bg=PANEL,
                                    anchor="w")
        self.kind_label.pack(fill="x")

        self.now_playing_var = tk.StringVar(value="")
        now_playing = tk.Label(inner, textvariable=self.now_playing_var,
                                font=(FONT_FAMILY, 15, "bold"), fg=TEXT, bg=PANEL,
                                wraplength=380, justify="left", anchor="w")
        now_playing.pack(fill="x", pady=(2, 10))

        self.progress = ProgressBar(inner, width=380, height=7, bg=PANEL,
                                     trough=TROUGH, fill=ACCENT)
        self.progress.pack(fill="x")

        time_row = tk.Frame(inner, bg=PANEL)
        time_row.pack(fill="x", pady=(4, 0))
        self.elapsed_var = tk.StringVar(value="0:00")
        self.duration_var = tk.StringVar(value="0:00")
        tk.Label(time_row, textvariable=self.elapsed_var, font=(FONT_FAMILY, 9),
                  fg=MUTED, bg=PANEL).pack(side="left")
        tk.Label(time_row, textvariable=self.duration_var, font=(FONT_FAMILY, 9),
                  fg=MUTED, bg=PANEL).pack(side="right")

        self.status_var = tk.StringVar(value="")
        status = tk.Label(self, textvariable=self.status_var,
                           font=(FONT_FAMILY, 10), fg=MUTED, bg=BG)
        status.pack()

        # ----- transport controls -----
        controls = tk.Frame(self, bg=BG)
        controls.pack(pady=16)

        self.play_btn = HoverButton(
            controls, text="\u25B6  Play", width=12, command=self.toggle_play,
            bg=ACCENT, fg="white", activebackground="#c1121f", activeforeground="white",
            hover_bg="#f2545f", borderwidth=0, font=(FONT_FAMILY, 11, "bold"), pady=6,
        )
        self.play_btn.grid(row=0, column=0, padx=6)

        skip_btn = HoverButton(
            controls, text="\u23ED  Skip", width=12, command=self.skip,
            bg=PANEL, fg=TEXT, activebackground="#2a2a2a", activeforeground=TEXT,
            hover_bg="#232323", borderwidth=0, font=(FONT_FAMILY, 11), pady=6,
        )
        skip_btn.grid(row=0, column=1, padx=6)

        # ----- volume -----
        vol_frame = tk.Frame(self, bg=BG)
        vol_frame.pack(pady=(2, 14))
        tk.Label(vol_frame, text="Volume", fg=MUTED, bg=BG,
                  font=(FONT_FAMILY, 10)).pack(side="left", padx=(0, 8))
        self.vol_slider = tk.Scale(
            vol_frame, from_=0, to=100, orient="horizontal",
            command=self._on_volume_change, bg=BG, fg=TEXT, troughcolor=PANEL,
            highlightthickness=0, length=200, showvalue=False, sliderrelief="flat",
            activebackground=ACCENT,
        )
        self.vol_slider.set(70)
        self.vol_slider.pack(side="left")
        self.vol_pct_var = tk.StringVar(value="70%")
        tk.Label(vol_frame, textvariable=self.vol_pct_var, fg=MUTED, bg=BG,
                  font=(FONT_FAMILY, 10), width=4).pack(side="left", padx=(6, 0))

        # ----- settings -----
        settings = tk.Frame(self, bg=BG)
        settings.pack(pady=(0, 10))

        self.news_enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            settings, text="Play news segments", variable=self.news_enabled_var,
            command=self._on_news_toggle, bg=BG, fg=TEXT, selectcolor=PANEL,
            activebackground=BG, activeforeground=TEXT, cursor="hand2",
            font=(FONT_FAMILY, 10), borderwidth=0, highlightthickness=0,
        ).pack(anchor="w")

        discord_row = tk.Frame(settings, bg=BG)
        discord_row.pack(anchor="w")
        self.discord_rpc_var = tk.BooleanVar(value=self.discord.available)
        discord_check = tk.Checkbutton(
            discord_row, text="Discord Rich Presence", variable=self.discord_rpc_var,
            command=self._on_discord_toggle, bg=BG, fg=TEXT, selectcolor=PANEL,
            activebackground=BG, activeforeground=TEXT, cursor="hand2",
            font=(FONT_FAMILY, 10), borderwidth=0, highlightthickness=0,
        )
        discord_check.pack(side="left")
        if not self.discord.available:
            discord_check.config(state="disabled")
            tk.Label(discord_row, text=" (unavailable)", fg=MUTED, bg=BG,
                      font=(FONT_FAMILY, 9)).pack(side="left")
        self.discord.set_enabled(self.discord_rpc_var.get())

    # ---------- thread-safe UI updates ----------

    def _schedule_ui(self, func):
        """Call this from any thread to safely run `func` on the main
        thread instead of touching widgets directly."""
        self._ui_queue.put(func)

    def _poll_ui_queue(self):
        try:
            while True:
                func = self._ui_queue.get_nowait()
                func()
        except queue.Empty:
            pass
        self.after(50, self._poll_ui_queue)

    def _poll_progress(self):
        """Runs on the main thread. Reads the player's position (cheap,
        just arithmetic on floats/timestamps) and redraws the bar --
        this is what makes the progress bar move smoothly."""
        if self._progress_active and self._clip_duration > 0:
            pos = self.player.get_position()
            frac = pos / self._clip_duration
            self.progress.set_fraction(frac)
            self.elapsed_var.set(fmt_time(pos))
            self.duration_var.set(fmt_time(self._clip_duration))
        self.after(PROGRESS_POLL_MS, self._poll_progress)

    def _discord_status(self, msg):
        self._schedule_ui(lambda m=msg: self.status_var.set(m))

    # ---------- wheel + station switching ----------

    def _on_wheel_select(self, index):
        station = self.stations[index]
        self._switch_station(station)

    def _switch_station(self, station):
        # Run the switch (including the fade dip and timeline simulation)
        # off the UI thread so the wheel stays responsive.
        threading.Thread(target=self._do_switch, args=(station,), daemon=True).start()

    def _do_switch(self, station):
        with self._switch_lock:
            # Dip the current audio out, like the brief drop when the
            # in-game wheel opens.
            if self.player.get_busy():
                self.player.fadeout(SWITCH_FADE_MS)
                time.sleep(SWITCH_FADE_MS / 1000)

            self._stop_flag.set()
            if self._play_thread and self._play_thread.is_alive():
                self._play_thread.join(timeout=1)
            self._stop_flag.clear()
            self._intro_channel = None
            self._progress_active = False

            self.current_station = station
            sequencer, clip, offset = self._simulate_to_now(station)
            self.sequencer = sequencer
            self._primed_clip = clip
            self._primed_offset = offset
            self.playing = True

            self._schedule_ui(lambda s=station: self._on_station_switched(s))

            self._play_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._play_thread.start()

    def _on_station_switched(self, station):
        """Runs on the main thread via _schedule_ui."""
        self.status_var.set(f"Tuned to {station.name}")
        self.wheel.set_center_label(station.name.upper())
        self.play_btn.config(text="\u23F8  Pause")

    def _simulate_to_now(self, station):
        """Build a deterministic Sequencer for `station` and fast-forward
        it from t=0 up to 'now' (relative to session start), so tuning in
        picks up wherever a continuously-running station would be instead
        of always restarting fresh. Returns (sequencer, clip, offset)."""
        seed = int(hashlib.sha1(station.id.encode()).hexdigest(), 16) % (2**32)
        sequencer = Sequencer(station, seed=seed, news_enabled=self.news_enabled_var.get())

        elapsed = time.time() - self._session_start
        cursor = 0.0
        clip = sequencer.next_clip()

        # Cap iterations so a very long-running session can't spin forever.
        for _ in range(20000):
            dur = get_duration(clip.path, DEFAULT_DURATIONS.get(clip.kind, 30.0))
            if cursor + dur > elapsed:
                return sequencer, clip, max(0.0, elapsed - cursor)
            cursor += dur
            clip = sequencer.next_clip()

        return sequencer, clip, 0.0

    # ---------- transport controls ----------

    def toggle_play(self):
        if not self.current_station:
            return
        if self.playing:
            self.playing = False
            self.player.pause()
            if self._intro_channel is not None:
                self._intro_channel.pause()
            self.play_btn.config(text="\u25B6  Play")
        else:
            self.playing = True
            self.player.unpause()
            if self._intro_channel is not None:
                self._intro_channel.unpause()
            self.play_btn.config(text="\u23F8  Pause")

    def skip(self):
        if self.current_station:
            self.player.fadeout(150)
            if self._intro_channel is not None:
                self._intro_channel.fadeout(150)

    def _on_volume_change(self, val):
        self.volume = int(val) / 100
        self.vol_pct_var.set(f"{int(val)}%")
        self.player.set_volume(self.volume)

    def _on_news_toggle(self):
        if self.sequencer is not None:
            self.sequencer.news_enabled = self.news_enabled_var.get()

    def _on_discord_toggle(self):
        self.discord.set_enabled(self.discord_rpc_var.get())

    # ---------- playback loop (background thread) ----------

    def _playback_loop(self):
        track_loaded = False
        self._intro_channel = None

        primed_clip, self._primed_clip = self._primed_clip, None
        primed_offset, self._primed_offset = self._primed_offset, 0.0

        while not self._stop_flag.is_set():
            if not self.playing:
                # Paused: get_busy() actually goes False while paused too,
                # so we must NOT treat this branch as "track finished" --
                # just wait here until playing resumes.
                time.sleep(0.15)
                continue

            needs_new_clip = not track_loaded or not self.player.get_busy()

            if needs_new_clip:
                if primed_clip is not None:
                    clip, start_offset = primed_clip, primed_offset
                    primed_clip = None
                else:
                    clip, start_offset = self.sequencer.next_clip(), 0.0

                duration = get_duration(clip.path, DEFAULT_DURATIONS.get(clip.kind, 30.0))
                label = ICONS.get(clip.kind, "")
                kind_text = KIND_LABELS.get(clip.kind, "On Air")
                self._schedule_ui(lambda l=label, name=clip.path.stem, k=kind_text,
                                   kind=clip.kind, dur=duration:
                                   self._on_clip_started(l, name, k, kind, dur))

                try:
                    achieved_offset = self.player.play(
                        clip.path, start_offset=start_offset, fade_ms=200, volume=self.volume
                    )
                    track_loaded = True
                except pygame.error as exc:
                    self._schedule_ui(lambda name=clip.path.name, e=exc:
                                       self.status_var.set(f"Couldn't play {name}: {e}"))
                    track_loaded = False
                    time.sleep(1)
                    continue

                self._clip_duration = duration
                self._clip_kind = clip.kind

                # Skip the song-announcement overlay if we're joining mid-song
                # via the timeline simulation -- the DJ "already said it"
                # before we tuned in.
                self._intro_channel = (
                    self._start_intro_overlay(clip.intro)
                    if clip.intro and achieved_offset < 0.5 else None
                )

                if clip.kind == "song" and self.discord_rpc_var.get():
                    start_ts = time.time() - achieved_offset
                    self.discord.update(
                        details=clip.path.stem,
                        state=f"Listening to {self.current_station.name}",
                        start_ts=start_ts,
                        large_text=self.current_station.name,
                    )
                elif self.discord_rpc_var.get():
                    self.discord.update(
                        details=kind_text,
                        state=self.current_station.name,
                        large_text=self.current_station.name,
                    )
            else:
                # Same track still going (or just resumed from a pause) --
                # just keep an eye on the DJ overlay so we can un-duck the
                # music once it finishes talking.
                if self._intro_channel is not None and not self._intro_channel.get_busy():
                    self.player.set_volume(self.volume)
                    self._intro_channel = None

            time.sleep(0.15)

    def _on_clip_started(self, label, name, kind_text, kind, duration):
        """Runs on the main thread via _schedule_ui."""
        self.now_playing_var.set(f"{label}  {name}")
        color = KIND_COLORS.get(kind, ACCENT)
        self.kind_var.set(kind_text.upper())
        self.kind_label.config(fg=color)
        self.progress.set_colors(fill=color)
        self.progress.set_fraction(0.0)
        self.elapsed_var.set("0:00")
        self.duration_var.set(fmt_time(duration))
        self._progress_active = True

    def _start_intro_overlay(self, intro_path):
        """Play a DJ song-announcement clip on top of the currently
        playing song, ducking the song's volume for the duration --
        this is how the game actually does it (overlaid, not a separate
        clip beforehand). Works best with wav/ogg clips; mp3 support for
        overlay playback depends on your system's SDL_mixer build."""
        try:
            sound = pygame.mixer.Sound(str(intro_path))
        except pygame.error as exc:
            self._schedule_ui(lambda name=intro_path.name, e=exc:
                               self.status_var.set(f"Couldn't play DJ overlay {name}: {e}"))
            return None

        self.player.set_volume(self.volume * DUCK_FACTOR)
        return sound.play()

    def _on_close(self):
        self._stop_flag.set()
        self.player.stop()
        self.discord.close()
        self.destroy()


if __name__ == "__main__":
    app = RadioApp()
    app.mainloop()
