#!/usr/bin/env python3
"""Fixtures for verify_front.mjs's Mac app checks. No real Messages data.

    mac_fixture.py make HOME        a fake ~/Library/Messages/chat.db and Contacts
    mac_fixture.py sqlite3 ARGS...  stands in for macOS /usr/bin/sqlite3 -readonly -json

The stand-in prints what sqlite3 -json prints: one JSON array of objects
keyed by column name, NULL as null, nothing at all for no rows. It refuses
(rc 3) any call without -readonly, and opens the file read-only regardless,
so a write would fail even if the flag were there.
"""
import json
import os
import sqlite3
import sys

APPLE_EPOCH = 978307200


def ns(unix_seconds: float) -> int:
    return int((unix_seconds - APPLE_EPOCH) * 1e9)


def typedstream(text: str) -> bytes:
    raw = text.encode()
    n = len(raw)
    size = bytes([n]) if n < 0x80 else b"\x81" + n.to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject" \
           b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + size + raw + b"\x86\x84\x02iI\x01"


def make(home: str) -> None:
    import time
    now = time.time()
    msgs = os.path.join(home, "Library", "Messages")
    os.makedirs(msgs, exist_ok=True)
    db = sqlite3.connect(os.path.join(msgs, "chat.db"))
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT, chat_identifier TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
            is_from_me INTEGER, date INTEGER, handle_id INTEGER, associated_message_type INTEGER, item_type INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
    """)
    db.execute("INSERT INTO handle VALUES (1, '+15551234567')")
    db.execute("INSERT INTO chat VALUES (1, NULL, '+15551234567')")
    rows = [
        (1, "fx-1", "Fixture text one", None, 0, ns(now - 3600), 1, 0, 0),
        (2, "fx-2", None, typedstream("Fixture text two, body only"), 0, ns(now - 1800), 1, 0, 0),
        (3, "fx-3", "Fixture reply", None, 1, ns(now - 600), 1, 0, 0),
        (4, "fx-4", "Loved “Fixture text one”", None, 0, ns(now - 500), 1, 2000, 0),
        (5, "fx-5", "Fixture too old", None, 0, ns(now - 40 * 86400), 1, 0, 0),
    ]
    db.executemany("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO chat_message_join VALUES (1, ?)", [(r[0],) for r in rows])
    db.commit()
    db.close()
    ab = os.path.join(home, "Library", "Application Support", "AddressBook")
    os.makedirs(ab, exist_ok=True)
    c = sqlite3.connect(os.path.join(ab, "AddressBook-v22.abcddb"))
    c.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
        INSERT INTO ZABCDRECORD VALUES (1, 'Ana', 'Fixture', NULL);
        INSERT INTO ZABCDPHONENUMBER VALUES (1, '(555) 123-4567');
    """)
    c.commit()
    c.close()
    print(json.dumps({"expected_stored": 3}))


def fake_sqlite3(args) -> int:
    if "-readonly" not in args or "-json" not in args:
        print("refused: not -readonly -json", file=sys.stderr)
        return 3
    rest = [a for a in args if a not in ("-readonly", "-json")]
    path, query = rest[0], rest[1]
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        rows = db.execute(query).fetchall()
    except sqlite3.Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if rows:
        print("[" + ",\n".join(json.dumps({k: r[k] for k in r.keys()}) for r in rows) + "]")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "make":
        make(sys.argv[2])
    else:
        sys.exit(fake_sqlite3(sys.argv[2:]))
