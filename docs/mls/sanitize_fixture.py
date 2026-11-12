#!/usr/bin/env python3
"""Turn a saved Matrix page into a fixture with nothing real left in it.

    python3 docs/mls/sanitize_fixture.py SAVED.html OUT.html --name "Your Name" [--name "Office Name"] [--seed N]

Guide: docs/mls/FIXTURE-CAPTURE.md. Offline: reads one file, writes one file.

What it does:
- drops every <script> (and keeps none of its text), <iframe> (a captcha
  frame becomes the stand-in /recaptcha/frame), <style>, <link>, <meta>
  other than charset, <img>, <noscript>, <base>, <object>, <embed>;
- drops event handlers and style attributes; links and form actions keep
  their path only (no query, no host) and off-site links become "#";
  every input loses its value; hidden inputs (view state, tokens) go;
- in result tables and labelled cards, by column (the same header names
  app/mls_matrix.py reads): addresses become "<n> Fixture St", MLS numbers
  9000xxxx, subdivisions "Fixture Subdivision <n>", prices move by up to
  10% (rounded to $1,000), square feet by up to 10% (rounded to 10), every
  date shifts by one random number of days (order kept); status, type,
  beds, baths, year built, zip, area and days on market stay; any column
  the reader does not map (agent, office, phone, remarks...) becomes
  "Redacted";
- everywhere: e-mail addresses, phone numbers, each --name, and runs of 6+
  digits are replaced.

Then it scans what it wrote and refuses to save (exit 1) if an e-mail, a
phone number, a --name, a hidden input, a script, or an off-site URL is
still there. Read the output yourself before committing it: this removes
what it knows to look for, nothing more.
"""
import argparse
import ast
import html
import random
import re
import sys
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path


def _columns():
    """app/mls_matrix.py's COLUMNS, read from the source (importing the app
    would need its configuration; this tool needs none)."""
    tree = ast.parse((Path(__file__).resolve().parents[2] / "app" / "mls_matrix.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "COLUMNS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("COLUMNS not found in app/mls_matrix.py")


COLUMNS = _columns()

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
LONG_DIGITS = re.compile(r"\b\d{6,}\b")
MONEY = re.compile(r"\$?\s*\d[\d,]*(?:\.\d+)?\s*[kKmM]?")
DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DROP = {"script", "iframe", "style", "link", "img", "noscript", "base", "object", "embed", "svg", "video", "audio"}
VOID = {"br", "hr", "input", "meta", "link", "img", "base", "col", "wbr", "source", "area", "param", "embed"}
CAPTCHA_SRC = re.compile(r"recaptcha|hcaptcha|turnstile|captcha", re.I)
KEEP_FIELDS = {"status", "property_type", "beds", "baths", "year_built", "zip_code", "area", "days_on_market",
               "distance_miles"}
PRICE_FIELDS = {"list_price", "sold_price"}
DATE_FIELDS = {"list_date", "pending_date", "sold_date"}
SAFE_EMAIL, SAFE_PHONE = "pat@agent.example", "(555) 010-0101"


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


class Sanitizer(HTMLParser):
    def __init__(self, names, seed):
        super().__init__(convert_charrefs=True)
        self.rng = random.Random(seed)
        self.days = self.rng.randint(-400, -30)
        self.names = sorted({n for n in names if n.strip()}, key=len, reverse=True)
        self.out = []
        self.skip = 0
        self.counters = {"address": 0, "mls_number": 0, "subdivision": 0}
        self.subdivisions = {}
        self.dropped = {"elements": 0, "hidden_inputs": 0, "handlers": 0}
        self.headers, self.col, self.in_th, self.th_text, self.in_cell = [], -1, False, "", None
        self.dt_label, self.in_dt, self.in_dd = "", False, False

    # ── values by field ──
    def field_value(self, field, text):
        t = text.strip()
        if not t:
            return text
        if field == "address":
            self.counters["address"] += 1
            return f"{100 + 2 * self.counters['address']} Fixture St"
        if field == "mls_number":
            self.counters["mls_number"] += 1
            return f"9000{self.counters['mls_number']:04d}"
        if field == "subdivision":
            if t not in self.subdivisions:
                self.subdivisions[t] = f"Fixture Subdivision {len(self.subdivisions) + 1}"
            return self.subdivisions[t]
        if field in PRICE_FIELDS:
            return self.money(t, 1000)
        if field == "sqft":
            return self.money(t, 10, dollars=False)
        if field in DATE_FIELDS:
            return self.dates(t)
        if field in KEEP_FIELDS:
            return self.text(t)
        return "Redacted"

    def money(self, t, step, dollars=True):
        m = MONEY.search(t)
        if not m:
            return "Redacted"
        raw = m.group(0).replace("$", "").replace(",", "").strip().lower()
        mult = 1000 if raw.endswith("k") else 1_000_000 if raw.endswith("m") else 1
        try:
            v = float(raw.rstrip("km")) * mult
        except ValueError:
            return "Redacted"
        v = round(v * self.rng.uniform(0.9, 1.1) / step) * step
        return (f"${int(v):,}" if dollars else f"{int(v):,}")

    def dates(self, t):
        def shift(m):
            try:
                d = date(int(m.group(3)), int(m.group(1)), int(m.group(2))) + timedelta(days=self.days)
            except ValueError:
                return "01/01/2000"
            return d.strftime("%m/%d/%Y")
        return DATE.sub(shift, t)

    def text(self, t):
        for n in self.names:
            t = re.sub(re.escape(n), "Pat Fixture", t, flags=re.I)
        t = EMAIL.sub(SAFE_EMAIL, t)
        t = PHONE.sub(SAFE_PHONE, t)
        t = LONG_DIGITS.sub(lambda m: "9" + "0" * (len(m.group(0)) - 1), t)
        return self.dates(t)

    # ── parsing ──
    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        a = dict(attrs)
        if tag == "iframe" and CAPTCHA_SRC.search(a.get("src") or ""):
            self.out.append('<iframe src="/recaptcha/frame" title="captcha"></iframe>')
            self.dropped["elements"] += 1
            if tag not in VOID:
                self.skip = 1
            return
        if tag in DROP or (tag == "meta" and "charset" not in a):
            self.dropped["elements"] += 1
            if tag not in VOID:
                self.skip = 1
            return
        if tag == "input" and (a.get("type") or "").lower() == "hidden":
            self.dropped["hidden_inputs"] += 1
            return
        kept = []
        for k, v in attrs:
            k = k.lower()
            if k.startswith("on") or k in ("style", "srcset", "integrity", "nonce", "value", "data-token"):
                self.dropped["handlers"] += k.startswith("on")
                continue
            if k in ("href", "action", "src", "formaction"):
                v = self.url(v or "")
            elif v is not None:
                v = self.text(v)
            kept.append((k, v))
        if tag == "tr":
            self.col = -1
        if tag in ("td", "th"):
            self.col += 1
            self.in_cell = tag
            self.in_th = tag == "th"
            self.th_text = ""
        if tag == "dt":
            self.in_dt, self.dt_label = True, ""
        if tag == "dd":
            self.in_dd = True
        self.out.append("<" + tag + "".join(f' {k}="{html.escape(v, quote=True)}"' if v is not None else f" {k}"
                                            for k, v in kept) + ">")

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in VOID:
                self.skip -= 1
            return
        if tag in DROP:
            return
        if tag == "th" and self.in_th:
            while len(self.headers) <= self.col:
                self.headers.append("")
            self.headers[self.col] = norm(self.th_text)
            self.in_th = False
        if tag in ("td", "th"):
            self.in_cell = None
        if tag == "table":
            self.headers = []
        if tag == "dt":
            self.in_dt = False
        if tag == "dd":
            self.in_dd = False
        if tag not in VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_th:
            self.th_text += data
            self.out.append(html.escape(self.text(data), quote=False))
            return
        field = None
        if self.in_cell == "td" and 0 <= self.col < len(self.headers) and self.headers[self.col]:
            field = self.field_of(self.headers[self.col])
        elif self.in_dd and self.dt_label:
            field = self.field_of(norm(self.dt_label))
        elif self.in_dt:
            self.dt_label += data
            self.out.append(html.escape(self.text(data), quote=False))
            return
        if self.in_cell == "td" or self.in_dd:
            if (self.in_cell == "td" and self.headers) or self.in_dd:
                self.out.append(html.escape(self.field_value(field, data) if data.strip() else data, quote=False))
                return
        self.out.append(html.escape(self.text(data), quote=False))

    def field_of(self, header):
        h = header.rstrip(":").strip()
        if "subdivision" in h or h in ("subdiv", "subdiv name"):
            return "subdivision"
        if h in COLUMNS:
            return COLUMNS[h]
        return "unmapped"

    def url(self, v):
        v = v.strip()
        if not v or v.startswith("#"):
            return v or "#"
        if re.match(r"^[a-z][a-z0-9+.-]*:", v, re.I) and not v.lower().startswith(("http:", "https:")):
            return "#"
        m = re.match(r"^(?:https?:)?//[^/]+(/[^?#]*)?", v, re.I)
        path = (m.group(1) or "/") if m else v.split("?", 1)[0].split("#", 1)[0]
        if m and not re.match(r"^(?:https?:)?//[^/]*(harmls|matrix)", v, re.I):
            return "#"
        return path if path.startswith("/") else "/" + path

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_comment(self, data):
        pass


def residue(text, names):
    found = []
    for m in EMAIL.finditer(text):
        if m.group(0) != SAFE_EMAIL:
            found.append(f"e-mail {m.group(0)[:3]}…")
    for m in PHONE.finditer(text):
        if m.group(0) != SAFE_PHONE:
            found.append("a phone number")
    for n in names:
        if n.strip() and n.lower() in text.lower():
            found.append(f"the name given as --name ({len(n)} characters)")
    if re.search(r"<script|type=\"?hidden", text, re.I):
        found.append("a script or hidden input")
    if re.search(r"https?://", text, re.I):
        found.append("a full URL")
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("saved")
    ap.add_argument("out")
    ap.add_argument("--name", action="append", default=[],
                    help="a real name to remove (yours, your office's, a client's); repeat as needed")
    ap.add_argument("--seed", type=int, default=None, help="fixed randomness, for a repeatable result")
    a = ap.parse_args()
    src = Path(a.saved).read_text(encoding="utf-8", errors="replace")
    s = Sanitizer(a.name, a.seed if a.seed is not None else random.SystemRandom().randrange(1 << 30))
    s.feed(src)
    s.close()
    body = "".join(s.out)
    text = ("<!-- Sanitized by docs/mls/sanitize_fixture.py from a saved Matrix page. Every address, number, "
            "price, date and contact is changed or removed. -->\n" + body + "\n")
    left = residue(text, a.name)
    print(f"addresses replaced {s.counters['address']}, MLS numbers {s.counters['mls_number']}, "
          f"subdivisions {len(s.subdivisions)}, elements dropped {s.dropped['elements']}, "
          f"hidden inputs dropped {s.dropped['hidden_inputs']}, dates shifted {s.days} days")
    if left:
        print("REFUSED: still in the output: " + "; ".join(sorted(set(left))) + ". Nothing was written.")
        return 1
    Path(a.out).write_text(text, encoding="utf-8")
    print(f"wrote {a.out}. Read it before committing, then add its .expect.json and run docs/mls/verify_mls.py "
          "(docs/mls/FIXTURE-CAPTURE.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
