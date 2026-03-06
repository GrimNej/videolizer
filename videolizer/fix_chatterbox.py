def patch_perth(log=None) -> None:
    """
    Patch missing PerthImplicitWatermarker with a compatible dummy implementation.

    Some chatterbox-tts dependency stacks expect this symbol to exist.
    Ported from Clip-Forge's fix_chatterbox.py.
    """
    try:
        import perth  # type: ignore

        if hasattr(perth, "PerthImplicitWatermarker"):
            return

        class PerthImplicitWatermarker:  # noqa: N801
            def __init__(self):
                if log:
                    log.warning("Using dummy PerthImplicitWatermarker (no watermarking)")

            def __call__(self, audio, *args, **kwargs):
                return audio

            def apply_watermark(self, audio, *args, **kwargs):
                return audio

            def detect_watermark(self, audio, *args, **kwargs):
                return None

            def decode(self, *args, **kwargs):
                return None

            def encode(self, *args, **kwargs):
                return None

        perth.PerthImplicitWatermarker = PerthImplicitWatermarker  # type: ignore[attr-defined]
        if log:
            log.info("Applied PerthImplicitWatermarker patch")
    except Exception as e:
        # If perth isn't installed, that's fine — chatterbox may not need it.
        if log:
            log.debug("Perth patch skipped", error=str(e))

