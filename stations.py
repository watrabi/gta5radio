"""
Loads radio stations from disk.

Expected folder layout (create this yourself from your extracted audio):

    stations/
        _shared/                (optional -- shared across ALL stations)
            ads/                 generic ad clips, not tied to one station
            news/                generic news clips, not tied to one station
        non_stop_pop/
            meta.json           (optional - display name / color)
            songs/                *.mp3 / *.wav / *.ogg / *.flac
            dj/                   generic DJ chatter + song announcements
            ads/                  station-specific TO_AD bumpers (see below)
            news/                 station-specific news content, if you have any
        radio_los_santos/
            songs/
            dj/
            ads/

Only "songs" is required for a station to show up. Everything else is
optional -- skip a folder and the station just won't do that part.

SONG-SPECIFIC DJ ANNOUNCEMENTS: if a clip in dj/ is named after a song
with a trailing _0, _1, etc. -- e.g. a song "Higher Ground.wav" paired
with dj/"Higher Ground_0.wav" and dj/"Higher Ground_1.wav" -- those are
treated as announcement variants for that specific song (one is picked
at random whenever that song comes up) rather than generic chatter.
They're matched up automatically.

TO_AD / TO_NEWS TRANSITION BUMPERS: real GTA stations reuse one shared
pool of ad/news audio across every station, but each station's own DJ
still has their own line leading into the break (e.g. "we'll be right
back"). If a clip in a station's ads/ folder is named starting with
"TO_AD" or "TO_NEWS" (any case, with or without a _0/_1 suffix), it's
treated as that station's own transition bumper into a break rather
than actual ad content -- it gets pulled out automatically and played
right before a clip from the shared ads/news pool, instead of being
treated as ad content itself.

TIME-OF-DAY DJ LINES: a clip in dj/ named MORNING_00, MORNING_01,
EVENING_00, etc. (AFTERNOON_/NIGHT_ also recognized) is only eligible
to play while the system clock is actually in that window -- morning
5am-noon, afternoon noon-5pm, evening 5pm-9pm, night 9pm-5am. They're
pulled out of the generic dj/ pool automatically and only added back
in as options when their time of day arrives.
"""

import json
import re
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac"}
INTRO_SUFFIX = re.compile(r"^(.*)_(\d+)$")
TO_AD_PATTERN = re.compile(r"^to[_ ]?ad", re.IGNORECASE)
TO_NEWS_PATTERN = re.compile(r"^to[_ ]?news", re.IGNORECASE)
DAYPART_PATTERN = re.compile(r"^(morning|afternoon|evening|night)(?:_\d+)?$", re.IGNORECASE)
SHARED_DIR_NAME = "_shared"


def _scan_folder(folder: Path):
    if not folder.exists():
        return []
    return sorted(f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTS)


class Station:
    def __init__(self, path: Path, shared_ads=None, shared_news=None):
        self.path = path
        self.id = path.name
        self.name = path.name.replace("_", " ").title()
        self.color = "#e63946"

        meta_path = path / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                self.name = meta.get("name", self.name)
                self.color = meta.get("color", self.color)
            except (json.JSONDecodeError, OSError):
                pass

        self.songs = self._scan("songs")
        self.dj = self._scan("dj")
        self.song_intros = self._match_song_intros()
        self.daypart_dj = self._match_dayparts()

        own_ads = self._scan("ads")
        self.to_ad_bumpers, self.to_news_bumpers, own_ads = self._split_bumpers(own_ads)

        self.ads = own_ads + list(shared_ads or [])
        self.news = self._scan("news") + list(shared_news or [])

    def _scan(self, subfolder):
        return _scan_folder(self.path / subfolder)

    def _match_song_intros(self):
        """Split dj/ into (a) song-specific announcement clips, keyed by
        the song's filename stem, and (b) everything else, which stays
        in self.dj as generic filler chatter."""
        song_stems = {s.stem for s in self.songs}
        intros = {}
        generic_dj = []

        for clip in self.dj:
            match = INTRO_SUFFIX.match(clip.stem)
            base = match.group(1) if match else None
            if base and base in song_stems:
                intros.setdefault(base, []).append(clip)
            else:
                generic_dj.append(clip)

        self.dj = generic_dj
        return intros

    def _match_dayparts(self):
        """Pull time-of-day DJ lines (MORNING_00, EVENING_00, etc.) out
        of the generic dj/ pool, keyed by daypart name. Whatever's left
        in self.dj stays as chatter usable any time of day."""
        dayparts = {}
        generic_dj = []

        for clip in self.dj:
            match = DAYPART_PATTERN.match(clip.stem)
            if match:
                key = match.group(1).lower()
                dayparts.setdefault(key, []).append(clip)
            else:
                generic_dj.append(clip)

        self.dj = generic_dj
        return dayparts

    @staticmethod
    def _split_bumpers(clips):
        """Pull TO_AD_* / TO_NEWS_* transition clips out of a station's
        ads/ folder; whatever's left is treated as genuine station-specific
        ad content (on top of the shared pool)."""
        to_ad, to_news, rest = [], [], []
        for clip in clips:
            if TO_AD_PATTERN.match(clip.stem):
                to_ad.append(clip)
            elif TO_NEWS_PATTERN.match(clip.stem):
                to_news.append(clip)
            else:
                rest.append(clip)
        return to_ad, to_news, rest

    def has_audio(self):
        return bool(self.songs)


def load_stations(stations_dir: Path):
    """Return a list of Station objects for every subfolder (other than
    _shared/) that contains songs."""
    stations = []
    if not stations_dir.exists():
        return stations

    shared_dir = stations_dir / SHARED_DIR_NAME
    shared_ads = _scan_folder(shared_dir / "ads")
    shared_news = _scan_folder(shared_dir / "news")

    for entry in sorted(stations_dir.iterdir()):
        if entry.is_dir() and entry.name != SHARED_DIR_NAME:
            station = Station(entry, shared_ads=shared_ads, shared_news=shared_news)
            if station.has_audio():
                stations.append(station)
    return stations
