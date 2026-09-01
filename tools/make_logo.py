"""Paint the Yamin app logo into /tmp as 512 and 120 PNGs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
OUT_512 = Path("/tmp/yamin-logo.png")
OUT_120 = Path("/tmp/yamin-logo-120.png")

SIZE = 512
TARGET_GLYPH_H = SIZE * 0.60
BG = (0x24, 0x42, 0x7C)
INK = (255, 255, 255)


def _bbox(font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    return ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), "Y", font=font)


def pick_font() -> ImageFont.FreeTypeFont:
    lo, hi = 1, SIZE * 4
    best: tuple[float, ImageFont.FreeTypeFont] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(str(FONT_PATH), mid)
        left, top, right, bottom = _bbox(font)
        height = bottom - top
        err = abs(height - TARGET_GLYPH_H)
        if best is None or err < best[0]:
            best = (err, font)
        if height < TARGET_GLYPH_H:
            lo = mid + 1
        else:
            hi = mid - 1
    assert best is not None
    return best[1]


def ink_centroid(img: Image.Image, bg: tuple[int, int, int]) -> tuple[float, float]:
    px = img.load()
    width, height = img.size
    sx = sy = n = 0
    for y in range(height):
        for x in range(width):
            if px[x, y] != bg:
                sx += x
                sy += y
                n += 1
    if n == 0:
        raise SystemExit("logo glyph produced no ink")
    return sx / n, sy / n


def paint(size: int, font: ImageFont.FreeTypeFont) -> Image.Image:
    canvas = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = _bbox(font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    draw.text((x, y), "Y", font=font, fill=INK)
    cx, cy = ink_centroid(canvas, BG)
    shifted = Image.new("RGB", (size, size), BG)
    shifted.paste(canvas, (round(size / 2 - cx), round(size / 2 - cy)))
    return shifted


def report(path: Path) -> None:
    with Image.open(path) as im:
        print(f"{path}: exists={path.is_file()} {im.size[0]}x{im.size[1]} {im.mode}")


def main() -> None:
    if not FONT_PATH.is_file():
        raise SystemExit(f"missing font: {FONT_PATH}")
    logo = paint(SIZE, pick_font())
    logo.save(OUT_512, "PNG")
    logo.resize((120, 120), Image.Resampling.LANCZOS).save(OUT_120, "PNG")
    report(OUT_512)
    report(OUT_120)


if __name__ == "__main__":
    main()
