"""
Voiceover generation: script text -> WAV/MP3.

Callable individually (e.g. from Discord: "make voiceover for this script")
or as part of the full pipeline.

Port logic from Clip-Forge voiceover_generator.py (Chatterbox TTS + calm style).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .jobs import JobLogger


def generate(text: str, out_path: Union[str, Path], log: "JobLogger") -> Path:
    """
    Generate voiceover from script text. Output WAV/MP3.
    """
    raise NotImplementedError(
        "voiceover.generate: Port from Clip-Forge voiceover_generator.py. "
        "Requires: chatterbox-tts, torch, torchaudio, pydub"
    )
