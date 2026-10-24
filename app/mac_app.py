"""Jarvis for Mac, zipped for download from Settings.

The app is mac-helper/Jarvis.app plus Contents/Resources/server.txt: this
tenant's public origin, so the person only types the pairing code. Nothing
secret goes in the zip; the code is typed, and the device token it buys stays
on the Mac.
"""
import io
import stat
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "mac-helper" / "Jarvis.app"
EXECUTABLE = "Contents/MacOS/Jarvis"


def _info(name: str, mode: int) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    zi.create_system = 3  # unix, so Archive Utility keeps the mode
    zi.external_attr = (mode & 0xFFFF) << 16
    zi.compress_type = zipfile.ZIP_DEFLATED
    return zi


def build(server: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        dirs = sorted({p.relative_to(APP_DIR.parent) for p in APP_DIR.rglob("*") if p.is_dir()} | {Path("Jarvis.app")})
        for d in dirs:
            z.writestr(_info(f"{d.as_posix()}/", stat.S_IFDIR | 0o755), b"")
        for p in sorted(APP_DIR.rglob("*")):
            if p.is_file() and p.name != "server.txt":
                rel = p.relative_to(APP_DIR).as_posix()
                mode = 0o755 if rel == EXECUTABLE else 0o644
                z.writestr(_info(f"Jarvis.app/{rel}", stat.S_IFREG | mode), p.read_bytes())
        z.writestr(_info("Jarvis.app/Contents/Resources/server.txt", stat.S_IFREG | 0o644), server.rstrip("/") + "\n")
    return buf.getvalue()
