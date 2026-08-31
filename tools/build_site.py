"""Render site/*.md into deployable HTML at /tmp/yamin-site."""

from __future__ import annotations

import re
from pathlib import Path

import markdown

SITE = Path(__file__).resolve().parents[1] / "site"
OUT = Path("/tmp/yamin-site")

PAGES = (
    ("index.md", "index.html"),
    ("privacy.md", "privacy.html"),
    ("terms.md", "terms.html"),
)

INTERNAL = {
    "index.md": "/",
    "privacy.md": "/privacy",
    "terms.md": "/terms",
}

LEFTOVER_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
INTERNAL_HREF = re.compile(
    r'href="(' + "|".join(re.escape(name) for name in INTERNAL) + r')"'
)

CSS = """\
body {
  font-family: Georgia, "Times New Roman", serif;
  max-width: 720px;
  margin: 2rem auto;
  padding: 0 1.25rem;
  line-height: 1.55;
  color: #222;
}
"""

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yamin</title>
<style>
{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def leftover_to_anchor(html: str) -> str:
    return LEFTOVER_LINK.sub(r'<a href="\2">\1</a>', html)


def rewrite_internal(html: str) -> str:
    return INTERNAL_HREF.sub(lambda m: f'href="{INTERNAL[m.group(1)]}"', html)


def render(source: str) -> str:
    body = markdown.markdown(source, extensions=["tables"])
    body = leftover_to_anchor(body)
    body = rewrite_internal(body)
    return TEMPLATE.format(css=CSS, body=body)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for src_name, dest_name in PAGES:
        dest = OUT / dest_name
        dest.write_text(render((SITE / src_name).read_text(encoding="utf-8")), encoding="utf-8")
        n = dest.read_text(encoding="utf-8").count("<a href")
        print(f"{dest_name}: {n} <a href")


if __name__ == "__main__":
    main()
