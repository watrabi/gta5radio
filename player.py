"""
Sequencing logic: decides what clip plays next for a given station.

This isn't a byte-for-byte reverse engineering of Rockstar's actual
scheduler (that's never been publicly documented), but it approximates
the *feel* of it:

  - Mostly songs, in random order, never repeating one of the last
    couple of tracks back to back.
  - Occasional DJ chatter between songs -- including time-of-day lines
    (MORNING_00, EVENING_00, etc.) that only become eligible while the
    system clock is actually in that window.
  - Occasional ad breaks: if the station has its own TO_AD transition
    line(s), one plays first, then an actual ad clip from the
    combined station+shared ad pool.
  - Occasional news breaks, same shape as ad breaks (TO_NEWS bumper
    then a news clip) -- can be toggled off entirely.

The RNG is seedable so the same station replayed with the same seed
always produces the same sequence -- this is what lets the app
fast-forward a station to "now" instead of always starting fresh
(see RadioApp._simulate_to_now in main.py). Note that time-of-day DJ
lines mean the exact sequence can still vary between runs made at
different times of day, since which pool of DJ lines is available
depends on the real clock, not just the seed.
"""

import datetime
import random
from collections import deque, namedtuple

# path: the clip to play through the main channel (song / dj / ad / news)
# kind: "song", "dj", "ad", or "news"
# intro: a DJ song-announcement clip to overlay on top of `path`
#        (only ever set when kind == "song"; None otherwise)
NextClip = namedtuple("NextClip", ["path", "kind", "intro"])

DAYPART_WINDOWS = (
    (5, 12, "morning"),
    (12, 17, "afternoon"),
    (17, 21, "evening"),
    # anything else (21:00-05:00) is "night"
)


def current_daypart(hour):
    """Map a 0-23 hour to 'morning' / 'afternoon' / 'evening' / 'night'."""
    for start, end, name in DAYPART_WINDOWS:
        if start <= hour < end:
            return name
    return "night"


class Sequencer:
    def __init__(self, station, seed=None,
                 dj_chance=0.30, ad_chance=0.20, songs_between_ads=3,
                 news_chance=0.12, songs_between_news=6, news_enabled=True,
                 no_repeat_window=3, clock_hour_fn=None):
        self.station = station
        self._rng = random.Random()

        self.dj_chance = dj_chance
        self.ad_chance = ad_chance
        self.songs_between_ads = songs_between_ads

        self.news_chance = news_chance
        self.songs_between_news = songs_between_news
        self.news_enabled = news_enabled  # flip live from the app at any time

        self.no_repeat_window = no_repeat_window

        # Lets tests (or anything else) inject a fake clock; defaults to
        # actually reading the system time.
        self._clock_hour_fn = clock_hour_fn or (lambda: datetime.datetime.now().hour)

        self._recent_songs = []
        self._songs_since_ad = 0
        self._songs_since_news = 0
        self._last_kind = None
        self._pending = deque()  # holds the ad/news clip queued behind a bumper

    def next_clip(self):
        """Return a NextClip(path, kind, intro)."""
        if self._pending:
            return self._pending.popleft()

        station = self.station

        if (self.news_enabled and station.news and self._last_kind != "news"
                and self._songs_since_news >= self.songs_between_news
                and self._rng.random() < self.news_chance):
            self._songs_since_news = 0
            self._last_kind = "news"
            return self._start_break(station.to_news_bumpers, station.news, "news")

        if (station.ads and self._last_kind != "ad"
                and self._songs_since_ad >= self.songs_between_ads
                and self._rng.random() < self.ad_chance):
            self._songs_since_ad = 0
            self._last_kind = "ad"
            return self._start_break(station.to_ad_bumpers, station.ads, "ad")

        daypart = current_daypart(self._clock_hour_fn())
        dj_pool = list(station.dj) + station.daypart_dj.get(daypart, [])
        if dj_pool and self._last_kind != "dj" and self._rng.random() < self.dj_chance:
            self._last_kind = "dj"
            return NextClip(self._rng.choice(dj_pool), "dj", None)

        pool = [s for s in station.songs if s not in self._recent_songs]
        if not pool:
            pool = station.songs
        song = self._rng.choice(pool)

        self._recent_songs.append(song)
        if len(self._recent_songs) > self.no_repeat_window:
            self._recent_songs.pop(0)

        self._songs_since_ad += 1
        self._songs_since_news += 1
        self._last_kind = "song"

        candidates = station.song_intros.get(song.stem)
        intro = self._rng.choice(candidates) if candidates else None

        return NextClip(song, "song", intro)

    def _start_break(self, bumpers, pool, kind):
        """Queue the actual ad/news clip; lead with the station's own
        transition bumper first if it has one."""
        content = NextClip(self._rng.choice(pool), kind, None)
        if bumpers:
            self._pending.append(content)
            return NextClip(self._rng.choice(bumpers), "dj", None)
        return content
