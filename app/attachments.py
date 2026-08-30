"""Attachment storage and the rules about what the model is allowed to see.

WHY FILES ON DISK AND IDS IN SQLITE
    Bytes do not belong in a row you read on every list render. Files live under
    data/attachments/{thread_id}/, the row carries only metadata, and the chat
    layer resolves ids -> bytes only for the one turn that needs them.

WHAT REACHES THE MODEL — the important distinction
    IMAGES are inlined into the chat request as base64 data URLs, because the
    gateway supports that natively (verified end to end in
    docs/v15-investigation.md) and there is no upload endpoint to hand it a URL.
    DOCUMENTS ARE NOT SENT AT ALL. The gateway explicitly rejects file/document
    parts with `unsupported_content_type`, so sending a PDF would fail the whole
    turn. We store them, show them, let the user download them, and mark them
    "saved, not readable yet". Document ingestion is a later phase and depends
    on the GBrain decision.

SIZE BUDGET
    The gateway caps a request body at 10,000,000 bytes and base64 inflates by
    4/3, so inlined images must stay well under that once history is included.
    Two independent caps below: MAX_UPLOAD_BYTES bounds what we will store,
    MAX_INLINE_TOTAL_BYTES bounds what we will inline in a single turn.
"""

import base64
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import threads

DATA_DIR = Path(
    os.environ.get(
        "JARVIS_ATTACHMENTS_DIR", str(Path(__file__).resolve().parents[1] / "data" / "attachments")
    )
).expanduser()

# Per file, on upload. Generous: the browser downscales photos before sending,
# so anything near this is a document.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Raw bytes of images inlined into ONE chat turn. 4 MB raw -> ~5.4 MB base64,
# leaving comfortable headroom under the gateway's 10 MB body cap for the
# conversation history that ships alongside it.
MAX_INLINE_TOTAL_BYTES = 4 * 1024 * 1024

# Anthropic accepts exactly these four image types. Anything else would be
# accepted here and then rejected upstream, which is a worse error to debug.
IMAGE_MIMES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Stored and downloadable, never sent to the model.
DOCUMENT_MIMES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    # Caption/transcript export from Meet, Zoom, Otter. Stored like any other
    # document (not sent to the model); the meeting-intake seam reads it.
    "text/vtt": ".vtt",
    "text/csv": ".csv",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

ALLOWED_MIMES = {**IMAGE_MIMES, **DOCUMENT_MIMES}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class AttachmentError(Exception):
    """Rejected upload. `.message` is safe to show the user verbatim."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_image(mime: str) -> bool:
    return mime in IMAGE_MIMES


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{max(1, n // 1024)} KB"


def safe_filename(name: str) -> str:
    """Keep it recognisable, strip anything that could escape the directory."""
    name = os.path.basename(name or "").strip() or "file"
    name = _SAFE_NAME.sub("_", name)
    return name[:120]


def _normalise_mime(mime: str, filename: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in ALLOWED_MIMES:
        return mime
    # Browsers sometimes send an empty or generic type; fall back to the
    # extension before rejecting outright.
    if mime in ("", "application/octet-stream"):
        # .vtt is not in every mimetypes table; a transcript upload must not
        # depend on the OS knowing the extension.
        if (filename or "").lower().endswith(".vtt"):
            return "text/vtt"
        guessed, _ = mimetypes.guess_type(filename or "")
        if guessed and guessed.lower() in ALLOWED_MIMES:
            return guessed.lower()
    return mime


def store(thread_id: str, filename: str, mime: str, data: bytes) -> Dict[str, Any]:
    """
    Validate and persist one upload. Raises AttachmentError with a message that
    is safe to render — the UI shows exactly why a file was refused.
    """
    if not data:
        raise AttachmentError("That file was empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AttachmentError(
            f"That file is {human_size(len(data))}. The limit is "
            f"{human_size(MAX_UPLOAD_BYTES)}.",
            status=413,
        )

    filename = safe_filename(filename)
    mime = _normalise_mime(mime, filename)
    if mime not in ALLOWED_MIMES:
        raise AttachmentError(
            f"{mime or 'That file type'} isn't supported. "
            "Images (JPEG, PNG, WebP, GIF) and documents (PDF, Word, Excel, CSV, text)."
        )

    if threads.get_thread(thread_id) is None:
        raise AttachmentError("No such conversation.", status=404)

    att_id = f"att_{uuid.uuid4().hex[:16]}"
    ext = ALLOWED_MIMES[mime]
    dest_dir = DATA_DIR / thread_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Stored under the opaque id, not the user's filename — two uploads called
    # "scan.pdf" must not collide, and the name never touches the path.
    path = dest_dir / f"{att_id}{ext}"
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    record = {
        "id": att_id,
        "thread_id": thread_id,
        "filename": filename,
        "mime": mime,
        "size": len(data),
        "ts": _now(),
        "path": str(path),
        "readable": is_image(mime),
    }

    conn = threads._ensure()
    with conn:
        conn.execute(
            "INSERT INTO attachments (id, thread_id, filename, mime, size, ts, path, readable) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record["thread_id"],
                record["filename"],
                record["mime"],
                record["size"],
                record["ts"],
                record["path"],
                1 if record["readable"] else 0,
            ),
        )
    return public(record)


def public(record: Dict[str, Any]) -> Dict[str, Any]:
    """The client-facing shape. Never leaks the on-disk path."""
    return {
        "id": record["id"],
        "thread_id": record["thread_id"],
        "filename": record["filename"],
        "mime": record["mime"],
        "size": record["size"],
        "size_human": human_size(record["size"]),
        "ts": record["ts"],
        "is_image": is_image(record["mime"]),
        # False for documents: stored and downloadable, but the model does not
        # see them. The UI renders this as "saved, not readable yet".
        "readable": bool(record["readable"]),
        "url": f"/api/attachments/{record['id']}",
    }


def get(att_id: str) -> Optional[Dict[str, Any]]:
    conn = threads._ensure()
    row = conn.execute("SELECT * FROM attachments WHERE id = ?", (att_id,)).fetchone()
    return dict(row) if row else None


def get_many(ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    conn = threads._ensure()
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM attachments WHERE id IN ({marks})", tuple(ids)
    ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    # Preserve the caller's order.
    return [by_id[i] for i in ids if i in by_id]


def public_many(ids: List[str]) -> List[Dict[str, Any]]:
    return [public(r) for r in get_many(ids)]


def inline_image_parts(ids: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Build OpenAI-style image parts for the chat request.

    Returns (parts, skipped_notes). Documents are skipped by design; oversized
    images are skipped rather than blowing the gateway's body cap, and both are
    reported so the caller can tell the user instead of silently dropping them.
    """
    parts: List[Dict[str, Any]] = []
    notes: List[str] = []
    budget = MAX_INLINE_TOTAL_BYTES

    for rec in get_many(ids):
        if not is_image(rec["mime"]):
            # Deliberate: the gateway rejects document parts outright.
            notes.append(f"{rec['filename']} (saved, not readable yet)")
            continue
        try:
            data = Path(rec["path"]).read_bytes()
        except OSError:
            notes.append(f"{rec['filename']} (could not be read back)")
            continue
        if len(data) > budget:
            notes.append(f"{rec['filename']} (too large to send this turn)")
            continue
        budget -= len(data)
        b64 = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{rec['mime']};base64,{b64}"},
            }
        )
    return parts, notes


def thread_attachments(thread_id: str) -> List[Dict[str, Any]]:
    conn = threads._ensure()
    rows = conn.execute(
        "SELECT * FROM attachments WHERE thread_id = ? ORDER BY ts ASC", (thread_id,)
    ).fetchall()
    return [public(dict(r)) for r in rows]
