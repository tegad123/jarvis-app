"""Front door admin, for the human on the server.

    python -m front.cli invite --note "Eddie's friend"      # prints a one-time link (7 days)
    python -m front.cli route tega 8780 --owner you@gmail.com
    python -m front.cli routes | signups | counters

The invite link is printed once, for you to send; only its hash is kept.
"""
import argparse
import json

from . import main as _main_cfg_only  # noqa: F401  (FRONT_* env checks run here too)
from . import store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Jarvis front door admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("invite")
    s.add_argument("--note", default="")
    s.add_argument("--days", type=int, default=7)
    s = sub.add_parser("route")
    s.add_argument("slug")
    s.add_argument("port", type=int)
    s.add_argument("--owner", default="")
    for name in ("routes", "signups", "counters"):
        sub.add_parser(name)
    a = p.parse_args(argv)
    conn = store.connect()
    if a.cmd == "invite":
        token = store.new_invite(conn, a.note, a.days)
        print(_main_cfg_only.url(_main_cfg_only.JOIN, f"/i/{token}"))
    elif a.cmd == "route":
        store.put_route(conn, a.slug, a.port, a.owner)
        print(f"{a.slug} -> 127.0.0.1:{a.port}")
    elif a.cmd == "routes":
        for r in conn.execute("SELECT slug, port, status, owner_email IS NOT NULL AS has_owner FROM routes "
                              "ORDER BY slug"):
            print(dict(r))
    elif a.cmd == "signups":
        for r in conn.execute("SELECT id, slug, status, detail, created_at FROM signups ORDER BY created_at"):
            print(dict(r))
    else:
        print(json.dumps(store.counters(conn), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
