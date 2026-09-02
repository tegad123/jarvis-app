"""Operator CLI for per-person accounts. There are no screens for this.

Onboarding is concierge (see ~/ONBOARDING_RUNBOOK.md), so account creation is an
operator action on the box, not a self-serve flow. Every PIN is shown ONCE here
and never again — the operator reads it to the person or stores it, exactly like
the provisioner's passphrase.

    python3 -m app.accounts_cli list
    python3 -m app.accounts_cli set-pin  <person-key> [--role owner|admin|member]
    python3 -m app.accounts_cli set-email <person-key> <email>
    python3 -m app.accounts_cli promote  <person-key> --role admin
    python3 -m app.accounts_cli disable  <person-key>
    python3 -m app.accounts_cli enable   <person-key>
"""

import argparse
import sys

from . import accounts


def _print_once(person_key: str, pin: str) -> None:
    print()
    print("  " + "!" * 62)
    print(f"  PIN FOR {person_key} — SHOWN ONCE, NOT RECOVERABLE.")
    print()
    print(f"      {pin}")
    print()
    print("  Give it to them directly. Re-running set-pin issues a NEW one and")
    print("  invalidates this. It is stored only as a salted hash.")
    print("  " + "!" * 62)


def cmd_list(_args) -> int:
    rows = accounts.roster()
    if not rows:
        print("No people in identity.people.")
        return 0
    print(f"{'PERSON':<12} {'ROLE':<8} {'PIN':<8} {'EMAIL':<8} {'STATE':<9} LAST LOGIN")
    for r in rows:
        print(f"{r['person_key']:<12} {r['role']:<8} "
              f"{'set' if r['has_pin'] else '—':<8} "
              f"{'set' if r['has_email'] else '—':<8} "
              f"{'disabled' if r['disabled'] else 'active':<9} "
              f"{r['last_login'] or 'never'}")
    print(f"\n{sum(1 for r in rows if r['has_pin'])} of {len(rows)} can sign in.")
    return 0


def cmd_set_pin(args) -> int:
    try:
        pin = accounts.set_pin(args.person_key, args.role)
    except accounts.AccountError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    acct = accounts.get(args.person_key)
    print(f"PIN set for {args.person_key} (role: {acct['role']}).")
    _print_once(args.person_key, pin)
    return 0


def cmd_set_email(args) -> int:
    try:
        accounts.set_email(args.person_key, args.email)
    except accounts.AccountError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"email set for {args.person_key}.")
    return 0


def cmd_role(args) -> int:
    try:
        acct = accounts.set_role(args.person_key, args.role)
    except accounts.AccountError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{args.person_key} is now {acct['role']}. They must sign in again for it to apply.")
    return 0


def cmd_disable(args) -> int:
    try:
        accounts.set_disabled(args.person_key, True)
    except accounts.AccountError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{args.person_key} disabled. Existing sessions stay valid until they expire — "
          f"the account simply cannot sign in again.")
    return 0


def cmd_enable(args) -> int:
    try:
        accounts.set_disabled(args.person_key, False)
    except accounts.AccountError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{args.person_key} enabled.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.accounts_cli", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="every registry person and their account state")

    p = sub.add_parser("set-pin", help="create or reset a PIN (prints it once)")
    p.add_argument("person_key")
    p.add_argument("--role", choices=accounts.ROLES, default=None)

    p = sub.add_parser("set-email", help="set the login email (shown as set/unset, not a Google flow)")
    p.add_argument("person_key")
    p.add_argument("email")

    p = sub.add_parser("promote", help="change a person's role")
    p.add_argument("person_key"); p.add_argument("--role", choices=accounts.ROLES, required=True)

    p = sub.add_parser("disable", help="block sign-in"); p.add_argument("person_key")
    p = sub.add_parser("enable", help="allow sign-in again"); p.add_argument("person_key")

    args = ap.parse_args()
    accounts.init()
    return {
        "list": cmd_list, "set-pin": cmd_set_pin, "set-email": cmd_set_email,
        "promote": cmd_role,
        "disable": cmd_disable, "enable": cmd_enable,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
