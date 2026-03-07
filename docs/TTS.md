# TTS (Text-to-Speech) / Voiceover

This document describes the Videolizer voiceover (TTS) feature in full: how it works, how to use it, how to configure it, and everything you might need to debug or extend it.

---

## Table of contents

1. [Overview](#overview)
2. [Where voiceover runs](#where-voiceover-runs)
3. [Public API](#public-api)
4. [Behavior and flow](#behavior-and-flow)
5. [Chatterbox TTS](#chatterbox-tts)
6. [Text processing](#text-processing)
7. [Calm style (post-processing)](#calm-style-post-processing)
8. [Placeholder / fallback mode](#placeholder--fallback-mode)
9. [Perth patch (fix_chatterbox)](#perth-patch-fix_chatterbox)
10. [Environment variables](#environment-variables)
11. [Dependencies](#dependencies)
12. [Output format](#output-format)
13. [Contracts (VideoPlan / future)](#contracts-videoplan--future)
14. [Troubleshooting](#troubleshooting)
15. [Clip-Forge parity](#clip-forge-parity)
16. [References](#references)

---

## Overview

The voiceover module turns **script text** into **speech audio** (WAV). It is:

- **Callable on its own** (e.g. from Discord: “make voiceover for this script”) via the `voiceover` CLI subcommand.
- **Used inside the full pipeline** as step 2: after the script is obtained (from the Remix Engine or internal content generation), voiceover produces `audio/voiceover.wav`, which is then used by sync, subtitles, and video assembly.

The implementation uses **Chatterbox TTS** when dependencies are available, with optional **calm-style** post-processing (slower speed, slight pitch shift, normalize, fades) to match Clip-Forge’s default voice. If Chatterbox or its dependencies fail, the module **falls back to a valid silent WAV** so the pipeline never hard-fails (useful in CI or minimal installs).

---

## Where voiceover runs

| Context | How it’s invoked |
|--------|-------------------|
| **Full pipeline** | `cli.cmd_full()` → step “voiceover” → `voiceover.generate(script, job_dir / "audio" / "voiceover.wav", log)` |
| **Standalone** | `python -m videolizer voiceover --text "Your script here." --out /path/to/audio.wav` |

In both cases the same `voiceover.generate()` function is used. The only difference is who provides the text and the output path, and whether a job directory exists for logging.

---

## Public API

**Module:** `videolizer.voiceover`

**Function:**

```python
def generate(text: str, out_path: Union[str, Path], log: "JobLogger") -> Path
```

- **`text`**: The narration script (plain string). It is normalized (whitespace collapsed) and optionally chunked before synthesis.
- **`out_path`**: File path for the output WAV. Parent directories are created if needed. **Only `.wav` is supported**; other extensions raise `ValueError`.
- **`log`**: A `JobLogger` instance (from `videolizer.jobs`) for `logs.txt` and `events.jsonl`. Required for consistent logging and step timing.

**Returns:** The resolved `Path` to the written WAV (same as `out_path` as a `Path`).

**Raises:**

- `ValueError` if `out_path` does not have a `.wav` extension.

All other failures (missing TTS deps, runtime errors) are caught internally: the function logs a warning and returns a **placeholder WAV** instead of raising.

---

## Behavior and flow

1. **Normalize** the input text (strip, collapse whitespace).
2. **Optional force placeholder:** If `VIDEOLIZER_VOICEOVER_PLACEHOLDER` is set to a truthy value, skip TTS and write a silent placeholder WAV; return.
3. **Try Chatterbox TTS:**  
   - Apply the Perth patch (see [Perth patch](#perth-patch-fix_chatterbox)).  
   - Load `ChatterboxTTS`, split text into chunks, synthesize each chunk, concatenate.  
   - If `style == "calm"` (default), run calm-style post-processing (speed, pitch, normalize, fades) via pydub; otherwise write the raw concatenated WAV.  
   - Return the path to the final WAV.
4. **On any exception:** Log a warning, then **fallback to placeholder WAV** and return.

So the pipeline always gets a valid WAV file; the only question is whether it contains real speech or silence.

---

## Chatterbox TTS

- **What it is:** [chatterbox-tts](https://github.com/nicolaiarley/chatterbox-tts) is the TTS engine used for actual speech synthesis. The model is loaded once per `generate()` call via `ChatterboxTTS.from_pretrained(device=...)`.
- **Device:** Controlled by `VIDEOLIZER_TTS_DEVICE`. Default is `cpu`. Use `cuda` if you have a GPU and want faster synthesis.
- **Sample rate:** The model’s native sample rate is read from `tts.sr` (often 22050 Hz). Chunks are saved at this rate; the final export (when pydub is used) is **44100 Hz** for compatibility with the rest of the pipeline.
- **Chunking:** Long scripts are split into chunks of at most `VIDEOLIZER_TTS_CHUNK_CHARS` characters (default 450) on **sentence boundaries** (see [Text processing](#text-processing)). Each chunk is synthesized separately and then concatenated.

Before importing Chatterbox/transformers, the code sets:

- `TRANSFORMERS_NO_TORCHVISION=1`
- `TORCHVISION_DISABLE_NMS_PATCH=1`

to avoid torchvision-related import issues in some environments.

---

## Text processing

- **Normalize:** `_normalize_text(text)` strips the string and collapses runs of whitespace to a single space. No other cleaning is applied in the current implementation.
- **Chunking:** `_split_text_into_chunks(text, max_chars)`:
  - Splits on sentence boundaries (after `.!?` followed by space).
  - Builds chunks such that each chunk has at most `max_chars` characters (configurable via `VIDEOLIZER_TTS_CHUNK_CHARS`, default 450).
  - Prevents mid-sentence cuts to keep intonation natural.

Word count is computed with `_count_words()` (regex `\b\w+\b`) and used for logging and for placeholder duration estimation.

---

## Calm style (post-processing)

When `VIDEOLIZER_VOICEOVER_STYLE` is `calm` (default), after concatenating TTS chunks the code applies:

1. **Speed:** Slow down playback to `VIDEOLIZER_VOICEOVER_SPEED` (default **0.75**, i.e. 75% speed → longer, calmer delivery).
2. **Pitch:** Shift pitch by `VIDEOLIZER_VOICEOVER_PITCH` (default **+0.15**, i.e. ~15% higher). Implemented via the frame-rate trick (change frame rate then resample back to original).
3. **Normalize:** pydub’s `normalize()` to bring levels to a consistent peak.
4. **Fades:** 100 ms fade-in and 100 ms fade-out to avoid hard edges.

All of this is done with **pydub** (and thus requires **FFmpeg** on the system). If pydub is not installed, the code uses a basic WAV concatenation path and **does not** apply calm style (raw concatenated audio is written).

---

## Placeholder / fallback mode

When to get a placeholder WAV:

- **Explicit:** `VIDEOLIZER_VOICEOVER_PLACEHOLDER` is set to a truthy value (e.g. `1`, `true`, `yes`, `on`). Used in CI or to force a quick run without TTS.
- **Implicit:** Chatterbox (or its deps) are missing or raise an exception; the module catches it and falls back to a silent WAV.

Placeholder behavior:

- A valid PCM WAV is written (16-bit, mono, sample rate from `VIDEOLIZER_VOICEOVER_SAMPLE_RATE`, default 22050).
- **Duration** is estimated from word count: `words / VIDEOLIZER_VOICEOVER_WPS` (default 2.2 words per second), clamped between `VIDEOLIZER_VOICEOVER_MIN_S` and `VIDEOLIZER_VOICEOVER_MAX_S` (defaults 3.0 and 120.0 seconds).
- Content is silence (zero samples). This keeps downstream steps (subtitles, video assembly) from failing when they expect a WAV of roughly the right length.

---

## Perth patch (fix_chatterbox)

Some versions of the Chatterbox/Resemble dependency stack expect a symbol `perth.PerthImplicitWatermarker`. In some environments this class is missing, which would cause an import or runtime error.

**What we do:** In `videolizer.fix_chatterbox`, before loading Chatterbox we call `patch_perth(log)`. If `perth` is importable but `PerthImplicitWatermarker` is missing, we attach a **dummy class** that implements the expected interface but leaves audio unchanged (no watermarking). If `perth` is not installed, we skip the patch and continue; Chatterbox may still work.

This logic was ported from Clip-Forge’s `fix_chatterbox.py` and adapted to use the Videolizer `JobLogger` for messages instead of `print`.

---

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `VIDEOLIZER_VOICEOVER_PLACEHOLDER` | (unset) | If set to `1`, `true`, `yes`, or `on`, skip TTS and write a silent placeholder WAV. |
| `VIDEOLIZER_TTS_DEVICE` | `cpu` | Device for Chatterbox: `cpu` or `cuda`. |
| `VIDEOLIZER_TTS_CHUNK_CHARS` | `450` | Max characters per TTS chunk; splitting is on sentence boundaries. |
| `VIDEOLIZER_VOICEOVER_STYLE` | `calm` | `calm` = apply speed/pitch/normalize/fades; any other value = raw concatenated audio only. |
| `VIDEOLIZER_VOICEOVER_SPEED` | `0.75` | Playback speed for calm style (0.75 = 75% speed, slower). |
| `VIDEOLIZER_VOICEOVER_PITCH` | `0.15` | Pitch shift for calm style (+0.15 ≈ +15% pitch). |
| `VIDEOLIZER_VOICEOVER_WPS` | `2.2` | Words per second used for placeholder duration estimate. |
| `VIDEOLIZER_VOICEOVER_MIN_S` | `3.0` | Minimum placeholder duration in seconds. |
| `VIDEOLIZER_VOICEOVER_MAX_S` | `120.0` | Maximum placeholder duration in seconds. |
| `VIDEOLIZER_VOICEOVER_SAMPLE_RATE` | `22050` | Sample rate for placeholder WAV. |
| `TRANSFORMERS_NO_TORCHVISION` | (set to `1` by code) | Avoid torchvision import inside transformers. |
| `TORCHVISION_DISABLE_NMS_PATCH` | (set to `1` by code) | Disable NMS patch for torchvision. |

---

## Dependencies

- **Minimal (placeholder only):** None beyond the Python stdlib (`wave`, `re`, etc.). The module will always be able to write a silent WAV.
- **Real TTS:**  
  - `torch`, `torchaudio`  
  - `chatterbox-tts`  
  - Optional: `resemble-perth` (if present, may need the Perth patch above).
- **Calm style (speed/pitch/normalize/fades):**  
  - `pydub`  
  - **FFmpeg** must be installed and on `PATH`; pydub uses it for encoding/decoding. Without FFmpeg, pydub may warn and calm style might not work correctly; the code may still write raw concatenated WAV if pydub is present.

Install full optional deps (from the repo root):

```bash
pip install ".[full]"
```

For Conda, create an environment and install the same; ensure `ffmpeg` is available (e.g. `conda install ffmpeg` or system FFmpeg).

---

## Output format

- **Format:** WAV (PCM).
- **Extension:** Only `.wav` is accepted for `out_path`; otherwise `ValueError` is raised.
- **When pydub is used (calm style or 44.1k export):** Sample rate is **44100 Hz**, mono, 16-bit.
- **When pydub is not used:** Sample rate is the model’s `tts.sr` (typically 22050 Hz), mono, 16-bit.
- **Placeholder:** Sample rate from `VIDEOLIZER_VOICEOVER_SAMPLE_RATE` (default 22050), mono, 16-bit, duration from word-count estimate.

Chunk files (e.g. `_tts_chunks/chunk_*.wav`) are temporary and removed after a successful run.

---

## Contracts (VideoPlan / future)

The Videolizer **contracts** define an `AudioStyle` in `VideoPlan` with `voice_id`, `pitch`, `speed`, `target_lufs`. Currently the **voiceover module does not read the VideoPlan**; it only receives the script text and uses environment variables for style (speed, pitch). In the future, the full pipeline could pass `plan_data["audio_style"]` into `voiceover.generate()` (or an extended API) so that speed/pitch/voice are driven by the Remix Engine per job.

---

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| **No sound in the WAV** | 1) Check logs for “falling back to placeholder”. 2) If placeholder was not forced, ensure `chatterbox-tts`, `torch`, `torchaudio` are installed and load without error. 3) Ensure FFmpeg is installed and on `PATH` if you use calm style. |
| **“Couldn't find ffmpeg or avconv”** | Install FFmpeg (e.g. `sudo apt-get install ffmpeg` on Debian/Ubuntu/WSL, or `conda install ffmpeg`). |
| **Placeholder when I want real TTS** | Unset `VIDEOLIZER_VOICEOVER_PLACEHOLDER`. Install the full optional deps and ensure Chatterbox loads; check `logs.txt` and `events.jsonl` for the exact exception. |
| **Voice too fast / too high** | Use calm style (default) and tune `VIDEOLIZER_VOICEOVER_SPEED` (default 0.75) and `VIDEOLIZER_VOICEOVER_PITCH` (default 0.15). Lower speed = slower; higher pitch value = higher pitch. |
| **PerthImplicitWatermarker / perth errors** | The `fix_chatterbox.patch_perth()` should run before Chatterbox is imported. If you see perth-related errors, ensure `patch_perth` is called at the start of `_generate_chatterbox` and that `resemble-perth` is installed if required by your chatterbox-tts version. |
| **Only raw audio, no calm style** | Install `pydub` and FFmpeg. If pydub is missing, the code uses a basic WAV concat and does not apply speed/pitch/normalize/fades. |

---

## Clip-Forge parity

The Videolizer TTS is intended to match Clip-Forge’s default voice behavior:

- **Engine:** Chatterbox TTS, same as Clip-Forge.
- **Chunking:** Same algorithm (sentence-boundary, 450 chars default).
- **Calm style:**  
  - Speed: **0.75** (Clip-Forge used 0.75 in code; comment in Clip-Forge said “0.85” but implementation used 0.75).  
  - Pitch: **+0.15** (configurable in Clip-Forge as `voice_pitch_shift`; we default to 0.15).  
  - Normalize + 100 ms fade-in/out.
- **Perth:** Same dummy `PerthImplicitWatermarker` patch, adapted to use `JobLogger` instead of `print`.

Differences:

- Videolizer uses **env vars** for all knobs; Clip-Forge used a config object.
- Videolizer has an explicit **placeholder fallback** so the pipeline never crashes when TTS is unavailable.
- Videolizer supports **forcing placeholder** via `VIDEOLIZER_VOICEOVER_PLACEHOLDER` for CI/minimal runs.

---

## References

- **Videolizer code:** `videolizer/voiceover.py`, `videolizer/fix_chatterbox.py`, `videolizer/cli.py` (voiceover subcommand and full pipeline).
- **Job logging:** `videolizer/jobs.py` (`JobLogger`, `step_start` / `step_end`).
- **Contracts:** `videolizer/contracts.py` (`AudioStyle`, `VideoPlan`).
- **Clip-Forge (reference):** `Clip-Forge/voiceover_generator.py`, `Clip-Forge/fix_chatterbox.py`.
- **Chatterbox TTS:** [chatterbox-tts](https://github.com/nicolaiarley/chatterbox-tts).
- **pydub:** [pydub](https://github.com/jiaaro/pydub) (requires FFmpeg).
