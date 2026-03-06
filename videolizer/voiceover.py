"""
Voiceover generation: script text -> WAV/MP3.

Callable individually (e.g. from Discord: "make voiceover for this script")
or as part of the full pipeline.

Port logic from Clip-Forge voiceover_generator.py (Chatterbox TTS + calm style).
"""

import os
import re
import wave
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .jobs import JobLogger


def generate(text: str, out_path: Union[str, Path], log: "JobLogger") -> Path:
    """
    Generate voiceover from script text.

    v1 behavior (CPU-friendly):
    - Always produces a valid WAV file even if heavy TTS dependencies are not installed.
    - If/when Chatterbox TTS is integrated, this function will produce real speech audio.
    """
    t0 = perf_counter()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.suffix.lower() not in (".wav",):
        raise ValueError(
            f"voiceover.generate currently supports only .wav output (got: {out_path.name})"
        )

    clean_text = _normalize_text(text)
    words = _count_words(clean_text)

    # Estimate duration for placeholder audio (used when real TTS isn't available yet).
    # Default: ~2.2 words/sec (~132 wpm). Allow tuning via env.
    wps = float(os.getenv("VIDEOLIZER_VOICEOVER_WPS", "2.2"))
    min_s = float(os.getenv("VIDEOLIZER_VOICEOVER_MIN_S", "3.0"))
    max_s = float(os.getenv("VIDEOLIZER_VOICEOVER_MAX_S", "120.0"))
    est_s = max(min_s, min(max_s, (words / max(wps, 0.1)) if words > 0 else min_s))

    sample_rate = int(os.getenv("VIDEOLIZER_VOICEOVER_SAMPLE_RATE", "22050"))
    channels = 1
    sampwidth = 2  # 16-bit PCM
    frames = int(est_s * sample_rate)

    log.info(
        "Generating placeholder voiceover WAV (silent)",
        output=str(out_path),
        words=words,
        estimated_seconds=est_s,
        sample_rate=sample_rate,
    )

    _write_silent_wav(
        out_path,
        sample_rate=sample_rate,
        channels=channels,
        sampwidth=sampwidth,
        frames=frames,
    )

    log.step_end("voiceover_generate", perf_counter() - t0, output=str(out_path))
    return out_path


def _normalize_text(text: str) -> str:
    # Collapse whitespace and remove some non-speechy clutter.
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def _write_silent_wav(
    path: Path, *, sample_rate: int, channels: int, sampwidth: int, frames: int
) -> None:
    # Write a valid PCM WAV filled with zero samples.
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)

        chunk_frames = 4096
        zero_frame = b"\x00" * (channels * sampwidth)
        remaining = frames
        while remaining > 0:
            n = min(chunk_frames, remaining)
            wf.writeframes(zero_frame * n)
            remaining -= n
