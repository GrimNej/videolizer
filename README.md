# Videolizer

Media Remix Engine for MofaClaw. Renders premium video shorts from a VideoPlan.

## Structure

- **contracts.py** – VideoPlan (input) and VideolizeResult (output) schemas
- **jobs.py** – Per-job folder creation, dual logging (logs.txt + events.jsonl)
- **content.py** – Script generation (character + series → TTS-optimized script)
- **voiceover.py** – TTS voiceover (callable individually or in pipeline)
- **sync.py** – Smart content sync (timed segments + image tags)
- **images.py** – Image search + crop + optional upscale
- **subtitles.py** – Whisper or duration-based subtitles
- **video.py** – MoviePy + FFmpeg assembly
- **music.py** – Background music mix

## Usage

```bash
# From mofaclaw repo root (or with PYTHONPATH=third_party/videolizer)
python -m videolizer full --plan plan.json --out ./job_dir
python -m videolizer voiceover --text "Hello world" --out audio.wav
python -m videolizer subtitles --text script.txt --audio voice.wav --out subs.srt
```

## Dependencies

Minimal (CLI + contracts + jobs): `python-dotenv`

Full pipeline: see `requirements.txt` and `pyproject.toml` optional deps.
Port logic from Clip-Forge; modules currently raise NotImplementedError.

## Job Layout

```
job_dir/
  job.json       # input plan
  result.json    # output result
  logs.txt       # human-readable logs
  events.jsonl   # structured events (for debugging)
  audio/
  images/
  video/
  music/
```
