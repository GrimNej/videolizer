"""
Content generation: character + series -> TTS-optimized script.

Port logic from Clip-Forge content_generator.py (Gemini 3-step workflow).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .jobs import JobLogger


def generate_script(character: str, series: str, log: "JobLogger") -> str:
    """
    Generate a TTS-optimized script from character and series.
    Uses Gemini to produce facts -> pick best -> rewrite for TTS.
    """
    raise NotImplementedError(
        "content.generate_script: Port from Clip-Forge content_generator.py. "
        "Requires: google-genai, langchain-google-genai, GOOGLE_API_KEY"
    )
