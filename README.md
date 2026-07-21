# Radio

A little desktop app that mimics a GTA V-style radio: pick a station,
it shuffles songs, occasionally cuts to DJ chatter or an ad break, and
avoids repeating the last couple of tracks.

It ships with **no audio** — you build stations out of your own
extracted files. This is for personal use with a copy of GTA V you own;
don't redistribute the extracted audio itself.

## 1. Install dependencies

```
pip install pygame
```

Optional, for Discord Rich Presence (see below):

```
pip install pypresence
```

(Python 3.9+ recommended. Tkinter comes with most Python installs already.)

## 2. Extract audio from GTA V

1. Download **OpenIV** (openiv.com) and point it at your GTA V install.
2. Navigate to `x64/audio/sfx/` — each radio station is an `.awc` file
   (Audio Wave Container), e.g. `RADIO_02_POP.awc`.
3. Right-click a station's `.awc` file → **Export to WAVE (.wav)** to
   dump every clip inside it (songs, DJ lines, ads, station IDs) as
   individual `.wav` files. For more control (and the JSON-like event
   list describing how Rockstar sequences things), export to
   **OpenFormats (OAC)** instead — it's more work to sort through, but
   gives you the metadata.
4. This produces a pile of unlabeled/oddly-named clips per station.
   You'll need to sort them by ear into songs / DJ bits / ads — there's
   no way around this part, it's the most tedious step.

   OpenIV also tends to dump each clip into its **own subfolder**
   instead of one flat folder. Use `flatten_audio.py` to fix that:

   ```
   python flatten_audio.py "OpenIV Export/RADIO_02_POP" flattened_pop
   ```

   This searches recursively, collects every `.mp3`/`.wav`/`.ogg`/`.flac`
   it finds no matter how deeply nested, and drops them all into one
   destination folder (renaming on the rare name collision instead of
   overwriting). Add `--move` to move the files instead of copying
   them, which also cleans up the empty folders left behind.

## 3. Organize into the `stations/` folder

Each station is a folder under `stations/`. Only `songs/` is required —
skip `dj/` or `ads/` for a station and it'll just play songs back to back.

```
stations/
    _shared/                <- optional, see "Shared ads/news" below
        ads/
        news/
    non_stop_pop/
        meta.json          <- optional, see below
        songs/
            track1.wav
            track2.wav
        dj/
            id_01.wav
            transition_01.wav
        ads/
            TO_AD_0.wav      <- this station's own transition into a break
            TO_NEWS_0.wav
    radio_los_santos/
        songs/
        ...
```

Supported formats: `.mp3`, `.wav`, `.ogg`, `.flac`.

### Shared ads/news library + TO_AD / TO_NEWS bumpers

In the real game, ad and news audio is one pool shared across every
station — each station's DJ just has their own line leading into the
break ("we'll be right back" / "let's check the news"). This app works
the same way:

- Put actual ad/news **content** in `stations/_shared/ads/` and
  `stations/_shared/news/`. Every station pulls from these pools.
- If a station has its own ad-adjacent clips named starting with
  `TO_AD` or `TO_NEWS` (any case, with or without a `_0`/`_1` suffix —
  e.g. `TO_AD_0.wav`, `to_news.wav`) sitting in its own `ads/` folder,
  those are automatically recognized as that station's transition
  bumper rather than ad content itself, and play right before a clip
  pulled from the shared pool.
- A station can still have its own extra ad content beyond the shared
  pool too — anything in `ads/` that *isn't* a `TO_AD`/`TO_NEWS` bumper
  just gets added on top of the shared ads for that station.
- Ads always play. News can be switched off entirely from a checkbox
  in the app ("Play news segments") — handy if you'd rather not deal
  with news audio at all, or don't have any of your own.

### Time-of-day DJ lines

If you've got clips like `MORNING_00.wav`, `MORNING_01.wav`, or
`EVENING_00.wav` in `dj/`, they're recognized automatically and only
become eligible to play while the system clock is actually in that
window:

| Prefix | Window |
|---|---|
| `MORNING_` | 5am – noon |
| `AFTERNOON_` | noon – 5pm |
| `EVENING_` | 5pm – 9pm |
| `NIGHT_` | 9pm – 5am |

Run the app at 8am and only `MORNING_*` lines (plus generic chatter)
are in the running; run it at 8pm and you'll hear `EVENING_*` instead.
Like the song-announcement clips, these are pulled out of the generic
`dj/` pool automatically so they don't show up outside their window.

### Song-specific DJ announcements

If you've got clips of the DJ actually calling out a song by name, name
them after the song with a trailing `_0`, `_1`, etc. and drop them in
`dj/` right alongside the generic chatter:

```
songs/
    Higher Ground.wav
dj/
    Higher Ground_0.wav
    Higher Ground_1.wav
    id_01.wav               <- generic chatter, unaffected
```

Whenever "Higher Ground" comes up, the app randomly picks one of its
matching announcement clips and **overlays it on top of the song**,
ducking the music's volume while the DJ talks and bringing it back up
once they stop — same as the actual game, rather than playing the
announcement as its own separate track beforehand. Songs with no
matching clips just play straight through.

These matched clips are pulled out of the generic `dj/` pool
automatically, so they won't also show up as random unrelated chatter.

Overlay playback works best with `.wav`/`.ogg` — mp3 support for
simultaneous (non-streaming) playback depends on your system's
SDL_mixer build.

### Optional: `meta.json`

Drop this in a station folder to control its display name:

```json
{
  "name": "Non-Stop-Pop FM",
  "color": "#ff4fa3"
}
```

Without it, the folder name is used (`non_stop_pop` → "Non Stop Pop").

## 4. Run it

```
python main.py
```

Pick a station from the wheel, hit Play. Skip jumps to the next clip
immediately; the sequencer picks what plays next each time (see
`player.py` if you want to tune how often DJ chatter, ads, or news show
up — `dj_chance`, `ad_chance`, `news_chance` and friends in the
`Sequencer` constructor).

## Stations run continuously, even when you're not listening

Every station is treated as broadcasting nonstop from the moment you
launch the app — same as real radio. Tuning into a station doesn't
restart it from the beginning; the app works out what that station
would currently be playing (and how far into it) based on how long
it's been running, and joins in right there. Tune away and come back
later and it'll have moved further along, not reset.

This is a *calculated* simulation rather than literally decoding audio
for every station simultaneously in the background — each station's
clip order is deterministic (same station always produces the same
sequence), so the app can fast-forward through that sequence using
each clip's duration to figure out where "now" lands, without wasting
CPU actually playing anything for stations nobody's tuned to.

## How the sequencing works

`player.py`'s `Sequencer` isn't a reverse-engineered copy of Rockstar's
actual scheduler (that's never been publicly documented) — it's an
approximation:

- Songs play in random order, never repeating one of the last few tracks.
- DJ chatter has a chance to play between songs (skipped if the station
  has no `dj/` clips).
- Ad breaks fire after a run of a few songs: the station's own `TO_AD`
  bumper first (if it has one), then a clip from the ads pool.
- News breaks work the same way as ad breaks, and can be turned off
  entirely from the checkbox in the app.

Tweak the probabilities in `Sequencer.__init__` to taste.

## Progress bar & mid-song tuning

The now-playing panel shows a live progress bar plus elapsed/total time
for whatever's currently on air. Since stations "run continuously" even
while you're not tuned in (see above), tuning into a station often means
joining a song already in progress -- the app now actually starts
playback partway through the file to match, instead of restarting it
from 0:00.

For mp3/ogg this uses pygame's normal mid-file start support. WAV files
are handled specially in `audio_backend.py`: SDL_mixer's WAV support
frequently ignores a mid-file start offset, so for WAV clips the app
slices the raw audio with Python's `wave` module and plays the trimmed
result directly, so tuning in mid-song works regardless of format.

## Discord Rich Presence (optional)

The app can show what's currently playing on your Discord profile.

1. `pip install pypresence`
2. Create your own (free) Discord Application at
   <https://discord.com/developers/applications> -> **New Application**,
   then copy the **Application ID** from the General Information page.
   Discord doesn't provide a shared app ID for third-party presences,
   so you need your own -- it takes about a minute.
3. Provide the ID either as an environment variable:

   ```
   DISCORD_CLIENT_ID=123456789012345678 python main.py
   ```

   or by pasting it into `DEFAULT_CLIENT_ID` at the top of `discord_rpc.py`.
4. Launch the app -- there's a "Discord Rich Presence" checkbox in the
   settings area to turn it on/off live; it's on by default whenever a
   client ID is configured.

If `pypresence` isn't installed, no ID is set, or Discord isn't running,
this feature just quietly disables itself and the rest of the app works
exactly the same.
