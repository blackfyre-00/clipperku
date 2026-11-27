# ShortsClipper — Build Plan

Paste YouTube URL(s) → download → transcribe (word timestamps) → LLM clip scoring →
render vertical clips with animated captions → preview, edit captions, download / ZIP / upload.

## Project structure

```
clipperku/
├── app/
│   ├── __init__.py
│   ├── main.py            FastAPI app, routes, static + media mounts, cleanup loop
│   ├── config.py          .env settings, paths, provider config
│   ├── errors.py          AppError -> clear user-facing messages
│   ├── schemas.py         Pydantic request/response models + settings validation
│   ├── jobs.py            in-memory job store, persistence, thread pool, polling API
│   └── static/
│       ├── index.html     single-page UI
│       ├── app.js         UI logic (polling, caption preview, render, ZIP, upload)
│       └── styles.css     design system
├── services/
│   ├── pipeline.py        orchestrates analyze + render per job
│   ├── download.py        yt-dlp + progress + friendly errors
│   ├── transcribe.py      faster-whisper word-level JSON (auto language, Thai/English)
│   ├── select.py          LLM scoring rubric + robust JSON + heuristic fallback
│   ├── captions.py        beats, ASS presets, SRT, translation, safe-area layout
│   ├── edit.py            ffmpeg: tighten, reframe/face, zoom, audio, b-roll, burn, loop
│   ├── broll.py           Pexels search + download + Ken Burns stills + credits
│   ├── youtube.py         OAuth + resumable upload with #Shorts
│   ├── cleanup.py         24h retention janitor
│   └── media.py           ffmpeg/ffprobe helpers, escaping, error mapping
├── static/                (served copy/symlink target: app/static is canonical)
├── outputs/               rendered clips (served at /media)
├── data/
│   ├── work/<job>/        downloads + transcripts + intermediates
│   ├── uploads/           optional music uploads
│   ├── broll/             Pexels cache
│   └── jobs/              persisted job JSON
├── tests/                 unit tests + synthetic end-to-end render test
├── scripts/
│   ├── make_demo_video.py synthetic video/transcript for offline testing
│   └── selftest.py        renders a real mp4 with captions, no network needed
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore
└── README.md
```

## Pipeline stages

1. **Download** — yt-dlp best mp4, progress hook → job progress (0–30%).
2. **Transcribe** — faster-whisper, word timestamps, auto language, JSON saved (30–65%).
3. **Select** — LLM (configurable) returns N clips with 4×0–10 rubric scores,
   hook type/text/reason, key point, b-roll terms; snap to sentence boundaries,
   strip greetings, sort by total score (65–100%).
4. **Review** — UI lists scored candidates; user ticks clips and renders.
5. **Render per clip** — silence/filler tightening → face-aware 9:16 crop → zoom
   pulses → audio denoise/loudnorm (+ducked music) → b-roll overlays → burned ASS
   captions + overlays → optional loop → H.264/AAC 30fps.
6. **Deliver** — preview player, per-clip download, ZIP, caption edit + re-burn,
   YouTube upload, 24h cleanup.

## Applied article tips

- Hook in first 3s; shock/question/promise; zero intro; front-load payoff; hook matches first frame.
- Animated 1–2 word captions, mixed case, high contrast, lower third above bottom 15%,
  accent on spoken word, proofread/edit + re-render, second-language track.
- Kill pauses >0.4s and fillers (um/uh/like/เอ่อ/อ่า), cut every 3–4s with subtle zoom,
  end on sentence boundaries slightly early.
- B-roll for mentioned nouns (2–3s), Ken Burns on stills, credited.
- Denoise first, loudness −14 LUFS, music ducked under voice (off by default).
- Specific CTA outro, optional loop back to opening, one key-point overlay.
- Export native 9:16; warn >60s; batch + ZIP + cleanup.

## Test gates

- Unit: sentence snapping, greeting trim, filler ranges, beat timing, ASS safe area, SRT, heuristic scores.
- Integration: synthetic video + fake transcript → real ffmpeg render → assert mp4 streams, captions, dimensions, ZIP.
- API: TestClient health, validation, settings defaults, caption endpoints.
