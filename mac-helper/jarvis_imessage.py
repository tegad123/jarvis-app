#!/usr/bin/env python3
"""Jarvis iMessage helper for your Mac: reads your Messages, never sends.

It opens ~/Library/Messages/chat.db read-only, takes messages from the last 30
days (then only new ones), puts contact names on them from your Contacts, and
sends them to your own Jarvis. Nothing here can send a text.

    python3 jarvis_imessage.py pair --server https://you.example.com --code ABCD-EFGH
    python3 jarvis_imessage.py sync        # once
    python3 jarvis_imessage.py install     # keep syncing in the background
    python3 jarvis_imessage.py uninstall   # stop, and forget this Mac's token

Reading chat.db needs Full Disk Access (System Settings > Privacy & Security >
Full Disk Access) for whatever runs this: Terminal when you run it yourself,
and the python3 shown by `install` when it runs in the background.

Only the Python standard library is used.
"""
import argparse
import glob
import json
import os
import platform
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, "Library", "Application Support", "Jarvis", "helper.json")
CHAT_DB = os.path.join(HOME, "Library", "Messages", "chat.db")
CONTACTS = os.path.join(HOME, "Library", "Application Support", "AddressBook")
LABEL = "com.jarvis.imessage"
AGENT = os.path.join(HOME, "Library", "LaunchAgents", LABEL + ".plist")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DAYS = 30
BATCH = 500
FDA = ("Can't read your Messages. Give Full Disk Access to {who} in System Settings > Privacy & Security > "
       "Full Disk Access, then try again.")


class Refused(Exception):
    pass


# ── config ───────────────────────────────────────────────────────────────────

def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_config(path, cfg):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, path)


# ── reading Messages ─────────────────────────────────────────────────────────

def apple_time(value):
    """message.date is nanoseconds since 2001-01-01 (seconds on old macOS)."""
    value = int(value or 0)
    return APPLE_EPOCH + timedelta(seconds=value / 1e9 if value > 10 ** 11 else value)


def apple_value(when):
    return int((when - APPLE_EPOCH).total_seconds() * 1e9)


def body_text(blob):
    """Recent macOS keeps the text only in attributedBody (an NSArchiver
    typedstream). The string follows the NSString class name: a '+' marker,
    then its length (one byte, or 0x81 + two bytes, 0x82 + three bytes)."""
    if not blob:
        return ""
    blob = bytes(blob)
    at = blob.find(b"NSString")
    if at < 0:
        return ""
    plus = blob.find(b"+", at + 8)
    if plus < 0 or plus + 1 >= len(blob):
        return ""
    i, n = plus + 1, blob[plus + 1]
    if n == 0x81:
        n, i = int.from_bytes(blob[i + 1:i + 3], "little"), i + 3
    elif n == 0x82:
        n, i = int.from_bytes(blob[i + 1:i + 4], "little"), i + 4
    else:
        i += 1
    return blob[i:i + n].decode("utf-8", "replace")


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 7 else ""


def contact_names(root):
    """Phone numbers and emails to names, from every Contacts source."""
    names = {}
    files = glob.glob(os.path.join(root, "Sources", "*", "AddressBook-v22.abcddb"))
    files += glob.glob(os.path.join(root, "AddressBook-v22.abcddb"))
    for path in files:
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            people = {pk: " ".join(x for x in (first, last) if x) or (org or "")
                      for pk, first, last, org in db.execute(
                          "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD")}
            for owner, number in db.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if people.get(owner) and phone_key(number):
                    names.setdefault(phone_key(number), people[owner])
            for owner, email in db.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if people.get(owner) and email:
                    names.setdefault(email.strip().lower(), people[owner])
            db.close()
        except sqlite3.Error as exc:
            print(f"note: skipped a Contacts file ({exc}); messages will show numbers instead of names",
                  file=sys.stderr)
    return names


def label(handle, names):
    if not handle:
        return ""
    name = names.get(handle.strip().lower()) or names.get(phone_key(handle))
    return f"{name} ({handle})" if name else handle


def read_messages(db_path, after_rowid, since, names, limit):
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.execute("SELECT 1 FROM message LIMIT 1")
    except sqlite3.Error as exc:
        if "authoriz" in str(exc) or "unable to open" in str(exc):
            raise Refused(FDA.format(who="Terminal (or the app running this)")) from exc
        raise
    rows = db.execute("""
        SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.is_from_me, m.date, h.id,
               c.display_name, c.chat_identifier
          FROM message m
          LEFT JOIN handle h ON h.ROWID = m.handle_id
          LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
          LEFT JOIN chat c ON c.ROWID = j.chat_id
         WHERE m.ROWID > ? AND m.date >= ? AND COALESCE(m.associated_message_type, 0) = 0
           AND COALESCE(m.item_type, 0) = 0
         GROUP BY m.ROWID ORDER BY m.ROWID LIMIT ?""", (after_rowid, apple_value(since), limit)).fetchall()
    last = db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]
    db.close()
    out = []
    for rowid, guid, text, attributed, from_me, date, handle, chat_name, chat_id in rows:
        text = text or body_text(attributed)
        out.append({"rowid": rowid, "guid": guid, "text": text, "is_from_me": bool(from_me),
                    "sent_at": apple_time(date).isoformat(), "sender": "" if from_me else label(handle, names),
                    "chat": chat_name or label(chat_id, names)})
    return out, last


# ── talking to Jarvis ────────────────────────────────────────────────────────

def post(cfg, path, body, token=None):
    headers = {"Content-Type": "application/json", "User-Agent": "jarvis-imessage-helper/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cfg.get("cf_id") and cfg.get("cf_secret"):
        headers["CF-Access-Client-Id"], headers["CF-Access-Client-Secret"] = cfg["cf_id"], cfg["cf_secret"]
    req = urllib.request.Request(cfg["server"].rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            if "json" not in (resp.headers.get("Content-Type") or ""):
                raise Refused("Jarvis didn't answer (a sign-in page came back instead). If your Jarvis is behind "
                              "Cloudflare Access, pair again with --cf-id and --cf-secret.")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail")
        except Exception:
            detail = None
        raise Refused(f"Jarvis said no (HTTP {exc.code}): {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise Refused(f"Can't reach {cfg['server']}: {exc.reason}") from exc


def sync(cfg_path, db_path, contacts_root):
    cfg = load_config(cfg_path)
    if not cfg.get("token"):
        raise Refused("This Mac isn't paired yet. Run the pair command with a code from Jarvis Settings.")
    names = contact_names(contacts_root)
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    after, sent, posted = int(cfg.get("last_rowid") or 0), 0, False
    while True:
        batch, last = read_messages(db_path, after, since, names, BATCH)
        if not batch:
            after = max(after, last)
            break
        msgs = [{k: v for k, v in m.items() if k != "rowid"} for m in batch if m["text"].strip()]
        if msgs:
            got = post(cfg, "/api/mac/messages", {"messages": msgs}, token=cfg["token"])
            sent, posted = sent + int(got.get("stored") or 0), True
        after = batch[-1]["rowid"]
        cfg["last_rowid"] = after
        save_config(cfg_path, cfg)
    if not posted:
        post(cfg, "/api/mac/messages", {"messages": []}, token=cfg["token"])
    cfg["last_rowid"], cfg["last_sync"] = after, datetime.now(timezone.utc).isoformat()
    save_config(cfg_path, cfg)
    return sent


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_pair(a):
    cfg = {"server": a.server.rstrip("/")}
    if a.cf_id or a.cf_secret:
        if not (a.cf_id and a.cf_secret):
            raise Refused("Give both --cf-id and --cf-secret, or neither.")
        cfg["cf_id"], cfg["cf_secret"] = a.cf_id, a.cf_secret
    got = post(cfg, "/api/mac/claim", {"code": a.code, "name": a.name or platform.node() or "Mac"})
    cfg.update(token=got["token"], device_id=got["device_id"], last_rowid=0)
    save_config(a.config, cfg)
    print("Paired. Now run: python3 jarvis_imessage.py sync")


def cmd_sync(a):
    n = sync(a.config, a.db, a.contacts)
    print(f"Synced {n} new message(s).")


def cmd_run(a):
    while True:
        try:
            n = sync(a.config, a.db, a.contacts)
            if n:
                print(f"{datetime.now().isoformat(timespec='seconds')} synced {n} new message(s)", flush=True)
        except Refused as exc:
            print(f"{datetime.now().isoformat(timespec='seconds')} {exc}", file=sys.stderr, flush=True)
            if "isn't connected" in str(exc) or "HTTP 401" in str(exc):
                return 2
        time.sleep(a.every)


def cmd_install(a):
    py = os.path.realpath(sys.executable)
    me = os.path.realpath(__file__)
    logs = os.path.join(HOME, "Library", "Logs", "jarvis-imessage.log")
    plist = {"Label": LABEL, "ProgramArguments": [py, me, "run", "--every", str(a.every)],
             "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 60,
             "StandardOutPath": logs, "StandardErrorPath": logs}
    os.makedirs(os.path.dirname(AGENT), exist_ok=True)
    with open(AGENT, "wb") as f:
        plistlib.dump(plist, f)
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", AGENT], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", AGENT], check=True)
    print("Syncing in the background every", a.every, "seconds. Log:", logs)
    print("Give Full Disk Access to this python too, or the background sync can't read Messages:\n  " + py)


def cmd_uninstall(a):
    if os.path.exists(AGENT):
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", AGENT], capture_output=True)
        os.remove(AGENT)
    if os.path.exists(a.config):
        os.remove(a.config)
    print("Stopped and forgot this Mac. To delete the synced texts too, tap Disconnect in Jarvis Settings.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Jarvis iMessage helper (reads only; never sends).")
    p.add_argument("--config", default=CONFIG)
    p.add_argument("--db", default=CHAT_DB)
    p.add_argument("--contacts", default=CONTACTS)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("pair")
    s.add_argument("--server", required=True)
    s.add_argument("--code", required=True)
    s.add_argument("--name")
    s.add_argument("--cf-id")
    s.add_argument("--cf-secret")
    sub.add_parser("sync")
    for name in ("run", "install"):
        s = sub.add_parser(name)
        s.add_argument("--every", type=int, default=60)
    sub.add_parser("uninstall")
    a = p.parse_args(argv)
    try:
        return {"pair": cmd_pair, "sync": cmd_sync, "run": cmd_run, "install": cmd_install,
                "uninstall": cmd_uninstall}[a.cmd](a) or 0
    except Refused as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
