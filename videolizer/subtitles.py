"""
Subtitle generation: audio + text -> SRT.

Callable individually or as part of full pipeline.
Uses Whisper for word-level timing when available, else duration-based.

Port logic from Clip-Forge subtitle_generator.py.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .jobs import JobLogger


def generate(script: str, audio_path: Union[str, Path], job_dir: Path, log: "JobLogger") -> Path:
    """Generate SRT from script + audio (used in full pipeline)."""
    raise NotImplementedError(
        "subtitles.generate: Port from Clip-Forge subtitle_generator.py. "
        "Requires: openai-whisper (optional), pydub"
    )


def generate_from_files(
    script_path: Union[str, Path],
    audio_path: Union[str, Path],
    out_path: Union[str, Path],
    log: "JobLogger",
) -> Path:
    """Generate SRT from script file + audio file (standalone use)."""
    raise NotImplementedError(
        "subtitles.generate_from_files: Port from Clip-Forge subtitle_generator.py. "
        "Requires: openai-whisper (optional), pydub"
    )
