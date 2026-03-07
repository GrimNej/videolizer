"""
Subtitle generation: audio + text -> SRT.

Callable individually or as part of full pipeline.

When available, this module uses **Whisper** (via the `whisper` python package /
`openai-whisper`) for word-level timings. If Whisper is not installed or fails,
it falls back to a **duration-based** algorithm that spreads words evenly across
the processed audio duration, grouped into readable subtitle chunks.

This is a port of Clip-Forge's `subtitle_generator.py`, adapted to use
Videolizer's `JobLogger` instead of print statements and to be callable as
simple functions rather than a class.
"""

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from .jobs import JobLogger


_WHISPER_MODEL: Optional[object] = None  # cached model instance


def generate(script: str, audio_path: Union[str, Path], job_dir: Path, log: "JobLogger") -> Path:
    """
    Generate SRT from script + audio (used in full pipeline).

    - Reads `script` (plain text narration).
    - Uses `audio_path` (processed voiceover, including any speed changes).
    - Writes `subtitles.srt` into `job_dir / "video"`.

    Returns the path to the generated SRT file.
    """
    job_dir = Path(job_dir)
    out_path = job_dir / "video" / "subtitles.srt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    return _generate_srt(script, Path(audio_path), out_path, log)


def generate_from_files(
    script_path: Union[str, Path],
    audio_path: Union[str, Path],
    out_path: Union[str, Path],
    log: "JobLogger",
) -> Path:
    """
    Generate SRT from script file + audio file (standalone use).

    - `script_path`: Path to a UTF-8 text file containing the narration.
    - `audio_path`: Path to the processed audio (WAV/MP3 supported by moviepy).
    - `out_path`: Path to the SRT file to write.
    """
    script_path = Path(script_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {script_path}")

    script = script_path.read_text(encoding="utf-8")
    return _generate_srt(script, Path(audio_path), out_path, log)


def _generate_srt(
    script: str,
    audio_path: Path,
    out_path: Path,
    log: "JobLogger",
) -> Path:
    """
    Core implementation: try Whisper-based subtitles first, then fall back to
    duration-based subtitles if Whisper is unavailable or fails.
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    script = _normalize_script(script)
    if not script:
        log.warning("Subtitle generation: script is empty; writing empty SRT", audio=str(audio_path))
        out_path.write_text("", encoding="utf-8")
        return out_path

    mode = os.getenv("VIDEOLIZER_SUBTITLES_MODE", "auto").lower()
    use_whisper = mode in ("auto", "whisper")

    if use_whisper:
        try:
            log.info("Attempting Whisper-based subtitles", audio=str(audio_path), mode=mode)
            _generate_srt_with_whisper(script, audio_path, out_path, log)
            return out_path
        except Exception as e:
            log.warning(
                "Whisper subtitles failed; falling back to duration-based subtitles",
                error=str(e),
            )

    log.info("Generating duration-based subtitles", audio=str(audio_path), mode=mode)
    _generate_srt_basic(script, audio_path, out_path, log)
    return out_path


def _normalize_script(text: str) -> str:
    """Normalize script text for subtitle generation."""
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _load_whisper_model(log: "JobLogger"):
    """
    Lazily load the Whisper model (if available) and cache it.

    Uses the `whisper` module provided by `openai-whisper`. Model size is
    configurable via VIDEOLIZER_SUBTITLES_WHISPER_MODEL (default: "base").
    """
    global _WHISPER_MODEL

    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL

    try:
        import whisper  # type: ignore
    except ImportError:
        log.info("Whisper not installed; subtitles will use duration-based mode only")
        return None

    model_name = os.getenv("VIDEOLIZER_SUBTITLES_WHISPER_MODEL", "base")
    log.info("Loading Whisper model", model=model_name)
    _WHISPER_MODEL = whisper.load_model(model_name)
    log.info("Whisper model loaded")
    return _WHISPER_MODEL


def _split_text_into_groups(text: str, max_words: int = 3) -> List[str]:
    """
    Split text into groups of up to `max_words` words.

    This mirrors Clip-Forge's behavior where each subtitle shows ~3 words to
    improve readability and match the Whisper word-timing grouping.
    """
    words = text.split()
    groups: List[str] = []

    for i in range(0, len(words), max_words):
        group = words[i : i + max_words]
        groups.append(" ".join(group))

    return groups


def _make_continuous_timings(
    group_timings: List[dict[str, Any]],
    *,
    overlap: float,
) -> List[dict[str, Any]]:
    """
    Adjust timings so subtitles form a continuous stream with small overlaps.
    """
    if not group_timings:
        return group_timings

    continuous: List[dict[str, Any]] = []

    for i, timing in enumerate(group_timings):
        new_timing = dict(timing)

        # Extend end time to reach slightly into the next subtitle.
        if i < len(group_timings) - 1:
            next_start = group_timings[i + 1]["start"]
            new_timing["end"] = next_start + overlap

        # Start slightly before the previous ends for smooth transitions.
        if i > 0:
            prev_end = continuous[i - 1]["end"]
            new_timing["start"] = max(0.0, prev_end - overlap)

        continuous.append(new_timing)

    return continuous


def _generate_srt_with_whisper(
    script: str,
    audio_path: Path,
    out_path: Path,
    log: "JobLogger",
) -> None:
    """
    Generate SRT using Whisper word-level timestamps.

    - Splits text into groups (default 3 words).
    - Uses Whisper to get word timestamps from audio.
    - Aggregates word timings per group.
    - Makes timings continuous with small overlaps.
    """
    model = _load_whisper_model(log)
    if model is None:
        raise RuntimeError("Whisper model not available")

    max_words = _get_env_int("VIDEOLIZER_SUBTITLES_WHISPER_GROUP_WORDS", 3)
    timing_offset = _get_env_float("VIDEOLIZER_SUBTITLES_TIMING_OFFSET", 0.0)
    overlap = _get_env_float("VIDEOLIZER_SUBTITLES_OVERLAP", 0.05)

    log.info(
        "Generating Whisper-based subtitles",
        audio=str(audio_path),
        max_words=max_words,
        timing_offset=timing_offset,
        overlap=overlap,
    )

    word_groups = _split_text_into_groups(script, max_words=max_words)

    # Run Whisper with word-level timestamps.
    result = model.transcribe(str(audio_path), word_timestamps=True)

    word_timings: List[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            w = word.get("word", "").strip()
            if not w:
                continue
            word_timings.append(
                {
                    "word": w,
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", 0.0)),
                }
            )

    log.info("Whisper word timings collected", count=len(word_timings))

    if not word_timings:
        raise RuntimeError("Whisper returned no word timings")

    group_timings: List[dict[str, Any]] = []
    word_index = 0

    for group in word_groups:
        group_word_count = len(group.split())
        if word_index >= len(word_timings):
            log.warning("No timings left for group", group=group)
            break

        start = word_timings[word_index]["start"] + timing_offset
        start = max(0.0, start)
        end_idx = min(word_index + group_word_count - 1, len(word_timings) - 1)
        end = word_timings[end_idx]["end"] + timing_offset

        group_timings.append({"text": group, "start": start, "end": end})
        word_index += group_word_count

    group_timings = _make_continuous_timings(group_timings, overlap=overlap)

    _write_srt_from_timings(group_timings, out_path, log)


def _generate_srt_basic(
    script: str,
    audio_path: Path,
    out_path: Path,
    log: "JobLogger",
) -> None:
    """
    Generate SRT using duration-based word timings (no Whisper).

    - Uses processed audio duration (after any speed changes).
    - Spreads words across the duration with a simple model.
    - Groups words into readable subtitle chunks.
    - Makes timings continuous with small overlaps.
    """
    from moviepy import AudioFileClip  # type: ignore

    timing_offset = _get_env_float("VIDEOLIZER_SUBTITLES_TIMING_OFFSET", 0.0)
    overlap = _get_env_float("VIDEOLIZER_SUBTITLES_OVERLAP", 0.05)
    words_per_group = _get_env_int("VIDEOLIZER_SUBTITLES_WORDS_PER_GROUP", 4)

    log.info(
        "Generating basic subtitles from audio duration",
        audio=str(audio_path),
        timing_offset=timing_offset,
        overlap=overlap,
        words_per_group=words_per_group,
    )

    with AudioFileClip(str(audio_path)) as audio:
        duration = float(audio.duration)

    if duration <= 0:
        raise ValueError(f"Audio duration is non-positive: {duration}")

    # Extract words; this pattern matches word characters between word boundaries.
    # NOTE: single backslashes are intentional (regex \b = word boundary).
    words = re.findall(r"\b\w+\b", script.lower())
    if not words:
        log.warning("No words found in script; writing empty SRT", audio=str(audio_path))
        out_path.write_text("", encoding="utf-8")
        return

    # Approximate per-word timing, scaled by word length for slight variation.
    duration_per_word = duration / len(words)
    word_timings: List[dict[str, float]] = []
    current_time = 0.0

    for w in words:
        start = max(0.0, current_time + timing_offset)
        word_duration = duration_per_word * (0.8 + len(w) * 0.05)
        end = start + word_duration
        word_timings.append({"word": w, "start": start, "end": end})
        current_time = end - timing_offset

    # Normalize to exactly fit the audio duration.
    total_estimated = word_timings[-1]["end"]
    if total_estimated > 0:
        scale = duration / total_estimated
        for t in word_timings:
            t["start"] *= scale
            t["end"] *= scale

    # Group into subtitle segments.
    groups: List[dict[str, Any]] = []
    for i in range(0, len(word_timings), words_per_group):
        chunk = word_timings[i : i + words_per_group]
        if not chunk:
            continue
        groups.append(
            {
                "text": " ".join(w["word"] for w in chunk),
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
            }
        )

    groups = _make_continuous_timings(groups, overlap=overlap)

    _write_srt_from_timings(groups, out_path, log)


def _write_srt_from_timings(
    timings: List[dict[str, Any]],
    out_path: Path,
    log: "JobLogger",
) -> None:
    """Write an SRT file from a list of {text, start, end} dicts."""
    lines: List[str] = []
    for idx, t in enumerate(timings, 1):
        start_ts = _format_timestamp(t["start"])
        end_ts = _format_timestamp(t["end"])
        text = str(t["text"]).upper()
        lines.append(f"{idx}\\n{start_ts} --> {end_ts}\\n{text}\\n")

    out_path.write_text("\\n".join(lines), encoding="utf-8")
    log.info("Subtitles written", path=str(out_path), segments=len(timings))


def _format_timestamp(seconds: float) -> str:
    """Format seconds to SRT timestamp (HH:MM:SS,mmm)."""
    if seconds < 0:
        seconds = 0.0
    millis = int((seconds - int(seconds)) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{millis:03}"

