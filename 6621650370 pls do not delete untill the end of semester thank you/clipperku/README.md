# ShortsClipper

Paste one or more YouTube links → download → word-level transcription → an LLM
scores the strongest moments → render vertical clips with animated, burned-in
captions → preview, edit, download, ZIP or upload to YouTube.

Built with FastAPI, yt-dlp, faster-whisper, ffmpeg and a single-page vanilla
frontend. No frontend build step, no database.

> **Only use videos you own or have permission to use.** Downloading or
> republishing someone else's video can violate copyright and YouTube's Terms
> of Service. This tool is for your own content, licensed material, or content
> you have explicit permission to edit.

---

## What it does

| Stage | What happens |
| --- | --- |
| **Download** | yt-dlp grabs the best MP4 with live progress and friendly error messages. |
| **Transcribe** | faster-whisper produces word-level timestamps, auto-detecting Thai / English. |
| **Score** | The LLM rates each candidate 0–10 on hook, standalone clarity, emotional intensity and payoff (total /40). Greetings, intros and mid-sentence starts are stripped. |
| **Choose** | You see every candidate with its score, reason, hook type and timing. Tick the ones worth exporting. |
| **Render** | Cuts pauses >0.4 s and fillers, reframes 9:16 on the speaker, adds subtle zoom cuts, denoises, normalises to −14 LUFS, optionally adds credited Pexels B-roll, burns animated captions. |
| **Deliver** | In-browser preview, per-clip MP4 download, SRT/ASS sidecars, ZIP, caption editing with re-burn, translation track, optional YouTube upload. |
| **Earn** | One-click Shorts metadata (title, description, hashtags, pinned comment), scheduled publishing, and an ownership check before upload. |

### Editing rules baked in

These come from the OpusClip video-editing checklist and are enforced by the
selector, the renderer or the UI:

- Hook lands in the first 3 seconds; shock / question / promise; no greeting, no channel intro.
- Payoff is front-loaded; the hook text matches the first frame.
- Captions are burned in. The default **Graphic Pill** shows short bold uppercase phrases in a rounded dark bubble (matching the reference shorts); switch to **Bold Karaoke** for 1–2 words per beat with a bright spoken-word highlight.
- Captions sit in the lower third but never inside the bottom 15 % platform-UI safe zone.
- Five caption presets: **Graphic Pill** (recommended default — bold uppercase words in a rounded dark bubble, like the reference shorts), **Cinema Label** (bold uppercase lower-left title label), **Bold Karaoke**, **Bold Pop**, **Minimal**, with a live phone preview.
- Color grades: **Punch** (default), **Cinematic** (warm/moody), **Neon** (purple/blue pop), **None**. Faster 2.5 s zoom cuts and uppercase captions are on by default to match the references.
- Pauses longer than 0.4 s and fillers (`um`, `uh`, `like`, `เอ่อ`, `อ่า`, …) are removed.
- Subtle zoom pulse every 3–4 seconds; clips end on sentence boundaries, slightly early.
- Face-aware reframe, with centre crop or blurred-background fallback.
- Audio: spectral denoise → −14 LUFS loudness normalisation; optional ducked music (off by default).
- Optional CTA outro in the last ~3 s, loop ending, key-point overlay and A/B hook variants.
- Optional 2–3 s B-roll for mentioned nouns, Ken Burns on stills, credited in the UI and ZIP.
- Export 1080×1920 / 1920×1080 / 1080×1080, H.264 + AAC, 30 fps; warns when a clip exceeds 60 s.
- Batch URLs, "Download all as ZIP", 24-hour cleanup, optional YouTube upload with `#Shorts`.

### Reference-short look and its limits

The default "Graphic" style was tuned from six shorts you supplied: minimal
speech-bubble text, bold uppercase labels, fast jump-cut zooms, heavier
contrast/colour, and optional cutaways. What an automated pipeline **cannot**
reproduce is a human editor's shot-by-shot timing, hand-drawn stickers, custom
countdown timers, or source footage that already contains printed title cards.
Enable **B-roll** (needs `PEXELS_API_KEY`) to get cutaway shots, use **Cinematic**
or **Neon** grade for the moody/gaming looks, and pick **Cinema Label** for the
lower-left documentary title style. The key-point overlay acts as the bold
uppercase label.

---

## Quick start (local)

### 1. Requirements

- **Python 3.11** recommended (faster-whisper and OpenCV wheels are most reliable there).
  Python 3.12/3.13 usually work. On Python 3.14 you can still run the whole app
  with `TRANSCRIBE_BACKEND=mock` and without OpenCV.
- **ffmpeg + ffprobe** on `PATH`. The app also auto-detects winget installs on Windows.
- **Node/Deno** is *not* required; yt-dlp may ask for one if YouTube changes its
  player signature (see troubleshooting).

Install ffmpeg:

```powershell
# Windows
winget install Gyan.FFmpeg
# macOS
brew install ffmpeg
# Debian/Ubuntu
sudo apt install ffmpeg
```

### 2. Install Python dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate         # macOS / Linux
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you are on a very new Python and `faster-whisper` or `opencv-python-headless`
has no wheel, install the rest and use the mock transcript backend:

```powershell
pip install fastapi "uvicorn[standard]" pydantic python-dotenv python-multipart httpx yt-dlp pytest
$env:TRANSCRIBE_BACKEND = "mock"
```

### 3. Configure

```powershell
Copy-Item .env.example .env
```

Open `.env` and set at least one of:

```dotenv
# Best default: an OpenAI-compatible key
LLM_PROVIDER=openai
LLM_API_KEY=sk-...

# Or Anthropic
# LLM_PROVIDER=anthropic
# LLM_API_KEY=sk-ant-...

# Or Google Gemini
# LLM_PROVIDER=gemini
# LLM_API_KEY=...

# Or a local Ollama server (no key needed)
# LLM_PROVIDER=ollama
# LLM_MODEL=llama3.1

# Optional B-roll
PEXELS_API_KEY=...

# Optional YouTube upload: path to a Google OAuth "Desktop app" client JSON
YT_CLIENT_SECRETS=C:\secrets\client_secret.json
```

With no LLM key the app still works: clip selection falls back to a
deterministic heuristic scorer and the UI says so. Adding `LLM_API_KEY` only
improves clip scoring and the auto-written metadata — it does **not** transcribe
audio or sync captions.

> **Captions that actually match your audio:** remove (or comment out)
> `TRANSCRIBE_BACKEND=mock` in `.env`. Mock mode uses sample sentences, so no
> amount of caption editing will make the words match the video. Real
> word-level timestamps come from faster-whisper, which needs Python 3.11 or
> the included Docker image (Python 3.14 crashes while loading the model).
> For clips already rendered, use **Sync to audio** on the result card: it
> detects speech with ffmpeg and re-times the caption beats onto it.

### 4. Run

```powershell
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>.

### 5. Try it without downloading anything

```powershell
python scripts/selftest.py
```

This builds a synthetic video, renders a real 9:16 MP4 with burned captions,
and validates streams, dimensions, fps, SRT/ASS and thumbnail. Add `--keep` to
inspect the files under `data/selftest/`.

---

## Docker

```powershell
docker build -t shortclipper .
docker run --rm -p 8000:8000 --env-file .env `
  -v ${PWD}/data:/app/data `
  -v ${PWD}/outputs:/app/outputs `
  shortclipper
```

The image pins Python 3.11 and installs ffmpeg plus Latin/Thai fonts. Mount
`data` and `outputs` so generated clips survive container restarts.

---

## How to use the UI

1. Paste one YouTube URL per line (batch mode supports up to `MAX_BATCH`, default 10).
2. Pick clip count, length range and aspect ratio.
3. Adjust captions, pacing, audio, B-roll, outro and A/B options. The phone
   preview updates live and shows the bottom-15 % safe zone.
4. Click **Find my best clips**. Watch per-source progress and logs.
5. Review the scored candidate cards. Each shows the rubric bars, total /40,
   hook type, timing and the LLM's reason. Select the ones you want.
6. Click **Render selected**. Rendering is queued in a background thread pool.
7. When a clip is done, use the player, **Download MP4**, **SRT**, **Write
   metadata**, **Edit captions** (re-burn without re-downloading),
   **Sync to audio** (re-time captions onto detected speech), **Translate**,
   or **Upload**.
8. **Write metadata** fills the upload dialog with a title, description,
   hashtags and a pinned comment. Edit them, optionally set a schedule, tick
   the ownership checkbox, then upload or queue the clip as private.
9. **Download all as ZIP** bundles every rendered clip plus captions, credits
   and a README.

---

## API

All endpoints return JSON except file downloads. Errors use:

```json
{ "error": { "code": "video_private", "message": "…", "hint": "…" } }
```

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | ffmpeg, LLM, Pexels, YouTube, retention status |
| `GET` | `/api/presets` | caption presets, aspect sizes, disclaimer |
| `POST` | `/api/analyze` | `{ urls: [...], settings: {...} }` → job |
| `GET` | `/api/jobs` | recent jobs |
| `GET` | `/api/jobs/{job_id}` | poll a job (sources, candidates, progress, logs) |
| `DELETE` | `/api/jobs/{job_id}` | delete a job and its files |
| `POST` | `/api/jobs/{job_id}/render` | `{ clip_ids: [...], settings?: {...} }` |
| `GET` | `/api/jobs/{job_id}/clips/{clip_id}/captions` | caption beats for editing |
| `POST` | `/api/jobs/{job_id}/clips/{clip_id}/captions` | save edits and re-burn |
| `POST` | `/api/jobs/{job_id}/clips/{clip_id}/translate` | add a translated caption track |
| `POST` | `/api/jobs/{job_id}/clips/{clip_id}/metadata` | generate title, description, hashtags and pinned comment |
| `POST` | `/api/jobs/{job_id}/clips/{clip_id}/resync` | re-time captions to speech detected in the clip's audio |
| `GET` | `/api/jobs/{job_id}/clips/{clip_id}/download` | download the rendered MP4 |
| `GET` | `/api/jobs/{job_id}/clips/{clip_id}/captions.srt` | download SRT (`.ass` also works) |
| `POST` | `/api/jobs/{job_id}/zip` | download a ZIP of rendered clips |
| `POST` | `/api/music` | upload background music (multipart `file`) |
| `GET` | `/api/youtube/status` | OAuth configuration / connection state |
| `GET` | `/api/youtube/auth` | returns the Google consent URL |
| `GET` | `/api/youtube/callback` | OAuth redirect handler |
| `POST` | `/api/youtube/upload` | `{ clip_id, title, description, privacy }` |
| `POST` | `/api/cleanup` | run the retention janitor now |

Rendered files are also served at `/media/<job>/<clip>/final.mp4`.

### Settings object

`ClipSettings` (all optional, validated by Pydantic):

```json
{
  "clip_count": 5,
  "min_length": 20,
  "max_length": 60,
  "aspect": "9:16",
  "captions_enabled": true,
  "caption_preset": "graphic",
  "caption_font": "",
  "caption_size": 82,
  "caption_color": "#FFFFFF",
  "highlight_color": "#FFE14D",
  "caption_position": 24,
  "caption_max_words": 2,
  "uppercase": true,
  "translate_to": "",
  "bilingual": true,
  "silence_removal": true,
  "remove_fillers": true,
  "pause_threshold": 0.4,
  "zoom_cuts": true,
  "zoom_interval": 2.5,
  "zoom_amount": 0.15,
  "color_grade": "punch",
  "reframe": "auto",
  "broll": false,
  "broll_max": 2,
  "key_point": true,
  "outro_enabled": true,
  "outro_text": "Follow for part 2",
  "loop_ending": false,
  "ab_variants": false,
  "denoise": true,
  "loudnorm": true,
  "music_file": "",
  "music_volume": 0.14
}
```

---

## Project structure

```
clipperku/
├── app/
│   ├── main.py          FastAPI routes, static + /media mounts, cleanup loop
│   ├── config.py        .env settings and paths
│   ├── errors.py        AppError → user-facing messages
│   ├── schemas.py       Pydantic request models and validation
│   └── jobs.py          in-memory job store, JSON persistence, thread pool
├── services/
│   ├── pipeline.py      analyze / render / caption-edit orchestration
│   ├── download.py      yt-dlp + progress + friendly errors
│   ├── transcribe.py    faster-whisper word timestamps (Thai/English) + mock
│   ├── select.py        LLM rubric, normalization, heuristic fallback
│   ├── captions.py      beats, ASS presets, SRT, translation, safe area
│   ├── edit.py          tighten, reframe, zoom, audio, B-roll, burn, loop
│   ├── broll.py         Pexels search/download, Ken Burns, credits
│   ├── youtube.py       OAuth + resumable upload with #Shorts
│   ├── cleanup.py       24-hour retention janitor
│   └── media.py         ffmpeg/ffprobe helpers and error mapping
├── static/
│   ├── index.html       single-page UI
│   ├── app.js           polling, preview, render, captions, ZIP, upload
│   ├── styles.css       design system
│   └── fonts/           optional embedded caption fonts
├── data/                downloads, transcripts, uploads, B-roll cache, jobs
├── outputs/             rendered clips, captions, thumbnails, ZIPs
├── scripts/
│   ├── make_demo_video.py
│   └── selftest.py
├── tests/
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

### Render pipeline (per clip)

1. `plan_keep_ranges` cuts pauses > `pause_threshold` and filler words.
2. `tighten` trims + concatenates ranges and applies denoise / loudnorm / ducked music.
3. `detect_face` samples 12 frames with OpenCV when available.
4. `_visual_pass` crops/reframes to the target aspect, adds the zoom pulse, and overlays B-roll with fades.
5. Caption beats are rebuilt from remapped word timings, then ASS + SRT are written.
6. `_burn` renders the final MP4 with libass; optional loop is appended.
7. A thumbnail is extracted. The intermediate `visual.mp4` is kept so caption
   edits can re-burn quickly without re-downloading.

---

## Configuration reference

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` | `openai`, `anthropic`, `gemini`, `openrouter`, `ollama`, `custom`, `mock` |
| `LLM_API_KEY` | – | Required for hosted providers |
| `LLM_MODEL` | provider default | e.g. `gpt-4o-mini`, `claude-sonnet-4-5` |
| `LLM_BASE_URL` | – | For custom OpenAI-compatible endpoints |
| `WHISPER_MODEL` | `small` | `tiny`…`large-v3`; larger = slower/better |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda` |
| `WHISPER_COMPUTE_TYPE` | `auto` | e.g. `int8`, `float16` |
| `TRANSCRIBE_BACKEND` | `faster-whisper` | Set `mock` for UI testing without a model |
| `PEXELS_API_KEY` | – | Enables B-roll |
| `YT_CLIENT_SECRETS` | – | Google OAuth desktop client JSON |
| `YT_CLIENT_ID` / `YT_CLIENT_SECRET` | – | Alternative to the JSON file |
| `YT_REDIRECT_URI` | `http://localhost:8000/api/youtube/callback` | Must match Google Cloud |
| `FFMPEG_BIN` / `FFPROBE_BIN` | auto | Absolute paths if not on PATH |
| `CAPTION_FONT` | auto | Thai-safe font selection by default |
| `CLEANUP_HOURS` | `24` | Retention window for generated media |
| `CLEANUP_INTERVAL_MINUTES` | `30` | Janitor interval |
| `MAX_BATCH` | `10` | URLs per analysis |
| `MAX_UPLOAD_MB` | `25` | Music upload limit |
| `YTDLP_COOKIES_FILE` | – | Netscape cookies for private/age-gated videos |
| `YTDLP_EXTRA_ARGS` | – | Space-separated `--key=value` options |

---

## Tests

```powershell
# fast unit + API tests
pytest

# real ffmpeg render test (about a minute)
$env:RUN_RENDER_TEST = "1"
pytest -m integration
Remove-Item Env:RUN_RENDER_TEST

# full offline self test with a printed checklist
python scripts/selftest.py --keep
```

The unit tests cover sentence snapping, greeting stripping, filler/pause ranges,
word remapping, crop geometry, caption beat timing, ASS safe margin, SRT,
heuristic scores and request validation.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ffmpeg was not found` | Install ffmpeg and restart the app, or set `FFMPEG_BIN` / `FFPROBE_BIN` in `.env`. On Windows the winget path is auto-detected. |
| `Speech transcription is unavailable` / job stuck at ~31% | On Python 3.14 the installed ctranslate2 build crashes while loading Whisper. Use Python 3.11 or the Docker image for real transcription; on 3.14 the app now fails fast with this explanation. Set `TRANSCRIBE_BACKEND=mock` in `.env` to test downloading, scoring, rendering and downloads without transcription. |
| `YouTube blocked the download` | `pip install -U yt-dlp`, install Node/Deno, or export cookies and set `YTDLP_COOKIES_FILE`. |
| Captions show boxes for Thai | Install a Thai font (Noto Sans Thai / Leelawadee UI), or drop a `.ttf` into `static/fonts/`. |
| Face reframe always centre-crops | `opencv-python-headless` is missing or no face was detected. The fallback is intentional. |
| Upload says dependencies are missing | `pip install google-api-python-client google-auth-oauthlib`, then set `YT_CLIENT_SECRETS`. |
| ZIP/download returns 404 after a while | The 24-hour janitor removed the files. Render again or raise `CLEANUP_HOURS`. |
| LLM call fails | The app falls back to the heuristic selector and shows a warning; check `LLM_PROVIDER`, key and model name. |

---

## Known limitations

- Jobs are held in memory (persisted as JSON). A server restart marks running
  jobs as failed; finished clips remain on disk.
- faster-whisper and OpenCV may not have wheels on brand-new Python releases.
  Docker pins Python 3.11 for this reason.
- YouTube upload requires your own Google Cloud OAuth client; the app never
  ships credentials.
- B-roll quality depends on Pexels search results and is intentionally off by
  default. Credits are written to `credits.txt`, shown in the UI and included
  in the ZIP.
- LLM selection is only as good as the transcript and the model. Review the
  scores before exporting.

---

## License / responsible use

Use this only with videos you own or have permission to edit. Respect
YouTube's Terms of Service, Pexels' license, and the rights of the people in
the footage. The UI and every ZIP export include this reminder.
