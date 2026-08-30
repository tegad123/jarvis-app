"""Push-to-talk transcription — local faster-whisper, no vendor, no key.

MODEL CHOICE (benchmarked on this box, 2 cores, int8 — 2026-08-17):
    tiny.en  RTF 0.08-0.12x  WER ~16-23%  ("Mire" for buyer, "troops" for groups)
    base.en  RTF 0.15-0.20x  WER ~6% on natural phrasing
  base.en wins: 5-7x faster than realtime, and tiny.en's errors are semantic —
  they would corrupt real requests, not just look scruffy. A 60s note
  transcribes in ~9s. Zero per-use cost was the decision gate for going local
  over OpenAI's API (which would have needed a key that does not exist).

MEMORY
    ~540 MB maxRSS with base.en resident, on a 3.8 GiB box with 3 GiB swap.
    The model LAZY-LOADS on the first request and then STAYS RESIDENT: load
    costs ~2.2s, and repeat push-to-talk is the expected pattern, so paying the
    load once beats paying it per-utterance. If memory pressure ever returns
    (see the OOM incident in the worklist), add an idle-unload here — it is a
    deliberate future knob, not an oversight.

NO RETENTION — voice notes are input, not records.
    Audio lands in a private spool dir, is transcribed, and is deleted in a
    finally block before the response is returned. Nothing else reads the dir.
    If voice notes ever become records (playback, audit), that is a product
    decision to make explicitly — not a default to drift into.

CONCURRENCY
    One transcription at a time (module lock). Two cores; two concurrent
    decodes would thrash both and slow each other past the point of either
    finishing sooner. Queued requests wait briefly rather than failing.
"""

import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

MODEL_NAME = "base.en"
COMPUTE_TYPE = "int8"

# Caps. Duration is the real bound (transcribe time scales with it); size is a
# transport sanity check — 120s of opus is ~2 MB, so 25 MB means "not audio".
MAX_DURATION_S = 120.0
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

SPOOL_DIR = Path(
    os.environ.get("JARVIS_TRANSCRIBE_SPOOL", "/root/jarvis-app/data/tmp-transcribe")
).expanduser()

_model = None
_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()


class TranscribeError(Exception):
    """Rejected or failed transcription. `.message` is safe to show verbatim."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


def _get_model():
    """Lazy-load once, keep resident. See the MEMORY note above."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel

                _model = WhisperModel(MODEL_NAME, device="cpu", compute_type=COMPUTE_TYPE)
    return _model


def _probe_duration(path: Path) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _normalize(src: Path, dst: Path) -> None:
    """Whatever the browser sent (webm/opus, mp4/aac, wav) -> 16k mono wav.

    ffmpeg does the format detection; if it cannot decode the blob, the upload
    was not audio and the error says so instead of a whisper stack trace.
    """
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-f", "wav", str(dst)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not dst.exists():
        detail = (proc.stderr or "").strip().split("\n")[-1][:120]
        raise TranscribeError(
            f"That recording could not be decoded as audio. ({detail})"
        )


def transcribe_bytes(data: bytes, content_type: str = "") -> Dict[str, Any]:
    """
    The whole pipeline: spool -> normalize -> whisper -> DELETE -> result.

    Raises TranscribeError with an honest, user-facing message on any refusal.
    """
    if not data:
        raise TranscribeError("The recording was empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TranscribeError(
            f"That recording is {len(data) // (1024*1024)} MB — too large to be audio.",
            status=413,
        )

    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    stem = SPOOL_DIR / f"ptt_{uuid.uuid4().hex[:12]}"
    raw = stem.with_suffix(".upload")
    wav = stem.with_suffix(".wav")

    try:
        raw.write_bytes(data)
        os.chmod(raw, 0o600)
        _normalize(raw, wav)

        duration = _probe_duration(wav)
        if duration is None:
            raise TranscribeError("Could not read the recording's duration.")
        if duration > MAX_DURATION_S:
            raise TranscribeError(
                f"That recording is {duration:.0f}s — the limit is "
                f"{MAX_DURATION_S:.0f}s. Try a shorter note.",
                status=413,
            )
        if duration < 0.3:
            raise TranscribeError("That recording is too short to contain speech.")

        model = _get_model()
        t0 = time.time()
        # Serialize decodes: two concurrent ones would thrash both cores.
        with _transcribe_lock:
            segments, _info = model.transcribe(
                str(wav), language="en", beam_size=5, vad_filter=True
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        elapsed = time.time() - t0

        if not text:
            raise TranscribeError(
                "No speech was detected in that recording.", status=422
            )

        return {
            "text": text,
            "duration_s": round(duration, 1),
            "model": MODEL_NAME,
            "transcribe_s": round(elapsed, 1),
        }
    finally:
        # NO RETENTION — delete before the response leaves, success or failure.
        for p in (raw, wav):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def spool_is_empty() -> bool:
    """True when nothing is retained. Exists so verification can assert it."""
    if not SPOOL_DIR.exists():
        return True
    return not any(SPOOL_DIR.iterdir())
