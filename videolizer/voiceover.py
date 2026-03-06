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
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from .jobs import JobLogger


def generate(text: str, out_path: Union[str, Path], log: "JobLogger") -> Path:
    """
    Generate voiceover from script text.

    Behavior:
    - If chatterbox-tts + torch deps are installed, generates real speech audio.
    - Otherwise, falls back to a valid silent WAV placeholder so the pipeline never hard-fails.
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

    # Allow forcing placeholder mode (useful in CI).
    if os.getenv("VIDEOLIZER_VOICEOVER_PLACEHOLDER", "").lower() in ("1", "true", "yes", "on"):
        return _generate_placeholder(clean_text, out_path, log, t0=t0)

    # Try real TTS first; fallback to placeholder on missing deps or runtime failure.
    try:
        return _generate_chatterbox(clean_text, out_path, log, t0=t0)
    except Exception as e:
        log.warning("Chatterbox TTS failed; falling back to placeholder WAV", error=str(e))
        return _generate_placeholder(clean_text, out_path, log, t0=t0)


def _generate_chatterbox(text: str, out_path: Path, log: "JobLogger", *, t0: float) -> Path:
    """
    Generate voiceover using Chatterbox TTS (CPU by default).

    Env knobs:
    - VIDEOLIZER_TTS_DEVICE=cpu|cuda
    - VIDEOLIZER_TTS_CHUNK_CHARS=450
    - VIDEOLIZER_VOICEOVER_STYLE=calm|raw
    - VIDEOLIZER_VOICEOVER_SPEED=0.75 (calm style)
    - VIDEOLIZER_VOICEOVER_PITCH=0.0  (frame-rate trick)
    """
    # Avoid torchvision import issues inside transformers/Chatterbox.
    os.environ.setdefault("TRANSFORMERS_NO_TORCHVISION", "1")
    os.environ.setdefault("TORCHVISION_DISABLE_NMS_PATCH", "1")

    from .fix_chatterbox import patch_perth

    patch_perth(log=log)

    import torch  # type: ignore
    import torchaudio  # type: ignore
    from chatterbox.tts import ChatterboxTTS  # type: ignore

    chunk_chars = int(os.getenv("VIDEOLIZER_TTS_CHUNK_CHARS", "450"))
    device = os.getenv("VIDEOLIZER_TTS_DEVICE", "cpu")
    style = os.getenv("VIDEOLIZER_VOICEOVER_STYLE", "calm").lower()

    log.info("Loading ChatterboxTTS model", device=device)
    tts = ChatterboxTTS.from_pretrained(device=device)
    sr = int(getattr(tts, "sr", 22050))

    chunks = _split_text_into_chunks(text, max_chars=chunk_chars)
    log.info("TTS chunking complete", chunks=len(chunks), chunk_chars=chunk_chars)

    # If pydub is available, use it for safe concat + post-processing.
    try:
        from pydub import AudioSegment  # type: ignore
        from pydub.effects import normalize  # type: ignore

        assembled = AudioSegment.silent(duration=0)
        chunk_dir = out_path.parent / "_tts_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for idx, chunk in enumerate(chunks, 1):
            log.info("Synthesizing chunk", idx=idx, total=len(chunks), chars=len(chunk))
            with torch.no_grad():
                wav = tts.generate(chunk)
            wav = _ensure_ct_torch(wav)

            chunk_path = chunk_dir / f"chunk_{idx}.wav"
            torchaudio.save(str(chunk_path), wav, sr)
            assembled += AudioSegment.from_wav(str(chunk_path))

        temp_path = out_path.with_name(out_path.stem + "_temp.wav")
        assembled.export(str(temp_path), format="wav", parameters=["-ar", "44100"])

        if style == "calm":
            speed = float(os.getenv("VIDEOLIZER_VOICEOVER_SPEED", "0.75"))
            pitch = float(os.getenv("VIDEOLIZER_VOICEOVER_PITCH", "0.0"))
            log.info("Applying calm style", speed=speed, pitch=pitch)

            audio = AudioSegment.from_wav(str(temp_path))
            audio = _change_speed_pydub(audio, speed)
            if pitch != 0.0:
                audio = _pitch_shift_pydub(audio, pitch)
            audio = normalize(audio).fade_in(100).fade_out(100)
            audio.export(str(out_path), format="wav", parameters=["-ar", "44100"])
        else:
            temp_path.replace(out_path)

        # Cleanup chunk dir
        try:
            for p in chunk_dir.glob("*.wav"):
                p.unlink(missing_ok=True)  # type: ignore[arg-type]
            chunk_dir.rmdir()
        except Exception:
            pass

        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

        log.step_end("voiceover_generate", perf_counter() - t0, output=str(out_path), sr=sr)
        return out_path
    except ImportError:
        # No pydub: do a basic concat using wave after torchaudio saves chunks.
        log.warning("pydub not installed; using basic WAV concat, no style processing")

        chunk_wavs: list[Path] = []
        for idx, chunk in enumerate(chunks, 1):
            log.info("Synthesizing chunk", idx=idx, total=len(chunks), chars=len(chunk))
            with torch.no_grad():
                wav = tts.generate(chunk)
            wav = _ensure_ct_torch(wav)

            chunk_path = out_path.parent / f"_chunk_{idx}.wav"
            torchaudio.save(str(chunk_path), wav, sr)
            chunk_wavs.append(chunk_path)

        _concat_wavs(chunk_wavs, out_path)
        for p in chunk_wavs:
            try:
                p.unlink()
            except Exception:
                pass

        log.step_end("voiceover_generate", perf_counter() - t0, output=str(out_path), sr=sr)
        return out_path

def _generate_placeholder(text: str, out_path: Path, log: "JobLogger", *, t0: float) -> Path:
    words = _count_words(text)

    # Estimate duration for placeholder audio.
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

    log.step_end("voiceover_generate", perf_counter() - t0, output=str(out_path), placeholder=True)
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


def _split_text_into_chunks(text: str, max_chars: int = 450) -> list[str]:
    text = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if not s:
            continue
        if len(cur) + len(s) + 1 <= max_chars:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks


def _ensure_ct_torch(wav):
    """Ensure waveform tensor is (C, T) on CPU float32."""
    import torch  # type: ignore

    if isinstance(wav, torch.Tensor):
        t = wav
    else:
        t = torch.as_tensor(wav)
    if t.ndim == 1:
        t = t.unsqueeze(0)
    elif t.ndim == 2 and t.shape[0] > t.shape[1]:
        t = t.transpose(0, 1)
    return t.detach().cpu().float()


def _concat_wavs(parts: list[Path], out_path: Path) -> None:
    """Concatenate WAV files with matching params using stdlib wave."""
    if not parts:
        raise ValueError("No wav parts to concat")
    with wave.open(str(parts[0]), "rb") as w0:
        params = w0.getparams()
        frames0 = w0.readframes(w0.getnframes())
    with wave.open(str(out_path), "wb") as wo:
        wo.setparams(params)
        wo.writeframes(frames0)
        for p in parts[1:]:
            with wave.open(str(p), "rb") as wi:
                if wi.getparams()[:4] != params[:4]:
                    raise ValueError("WAV parts have mismatched params")
                wo.writeframes(wi.readframes(wi.getnframes()))


def _change_speed_pydub(audio, speed_factor: float):
    """Change audio speed using frame-rate method (avoids artifacts for slow-down)."""
    if speed_factor == 1.0:
        return audio
    if speed_factor <= 0:
        return audio
    altered = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed_factor)})
    return altered.set_frame_rate(audio.frame_rate)


def _pitch_shift_pydub(audio, pitch_factor: float):
    """Pitch shift using frame-rate trick. pitch_factor +0.1 => +10% pitch."""
    if pitch_factor == 0.0:
        return audio
    rate_factor = 1.0 + pitch_factor
    shifted = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * rate_factor)})
    return shifted.set_frame_rate(audio.frame_rate)
