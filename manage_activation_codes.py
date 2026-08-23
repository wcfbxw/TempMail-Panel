#!/usr/bin/env python3
import argparse
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import app


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code():
    groups = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "LM-" + "-".join(groups)


def build_parser():
    parser = argparse.ArgumentParser(description="Manage LightningMail activation codes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate activation codes")
    generate.add_argument("--count", type=int, default=1, help="Number of codes (1-100)")
    generate.add_argument("--uses", type=int, default=1, help="Maximum uses per code")
    generate.add_argument("--days", type=int, default=0, help="Validity in days; 0 means no expiry")

    subparsers.add_parser("list", help="List code status (full codes are never stored)")

    revoke = subparsers.add_parser("revoke", help="Revoke an activation code")
    revoke.add_argument("code", help="Full activation code to revoke")
    return parser


def generate_codes(conn, count, max_uses, valid_days):
    if not 1 <= count <= 100:
        raise SystemExit("--count must be between 1 and 100")
    if not 1 <= max_uses <= 10000:
        raise SystemExit("--uses must be between 1 and 10000")
    if not 0 <= valid_days <= 3650:
        raise SystemExit("--days must be between 0 and 3650")

    created_at = app.utc_now_iso()
    expires_at = None
    if valid_days:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=valid_days)
        ).replace(microsecond=0).isoformat()

    codes = []
    for _ in range(count):
        while True:
            code = make_code()
            digest = app.activation_code_hash(code)
            try:
                conn.execute(
                    """
                    INSERT INTO activation_codes
                        (code_hash, code_hint, max_uses, used_count, created_at, expires_at, is_active)
                    VALUES (?, ?, ?, 0, ?, ?, 1)
                    """,
                    (digest, f"****-{code[-4:]}", max_uses, created_at, expires_at),
                )
                codes.append(code)
                break
            except sqlite3.IntegrityError:
                continue
    conn.commit()

    print("Activation codes generated. They are shown only once:")
    for code in codes:
        print(code)
    print(f"Uses per code: {max_uses}")
    print(f"Expires: {expires_at or 'never'}")


def list_codes(conn):
    rows = conn.execute(
        """
        SELECT code_hint, max_uses, used_count, created_at, expires_at, is_active
        FROM activation_codes
        ORDER BY created_at DESC, rowid DESC
        """
    ).fetchall()
    if not rows:
        print("No activation codes found.")
        return

    now = app.utc_now_iso()
    print(f"{'CODE':<14} {'USES':<11} {'STATUS':<9} {'EXPIRES'}")
    for hint, max_uses, used_count, _, expires_at, is_active in rows:
        if not is_active:
            status = "inactive"
        elif expires_at and expires_at <= now:
            status = "expired"
        elif used_count >= max_uses:
            status = "used"
        else:
            status = "active"
        print(f"{hint:<14} {f'{used_count}/{max_uses}':<11} {status:<9} {expires_at or 'never'}")


def revoke_code(conn, code):
    digest = app.activation_code_hash(code)
    if not digest:
        raise SystemExit("A valid activation code is required")
    cursor = conn.execute(
        "UPDATE activation_codes SET is_active=0 WHERE code_hash=?",
        (digest,),
    )
    conn.commit()
    if not cursor.rowcount:
        raise SystemExit("Activation code not found")
    print("Activation code revoked.")


def main():
    args = build_parser().parse_args()
    app.init_db()
    with sqlite3.connect(app.DB_FILE, timeout=10) as conn:
        if args.command == "generate":
            generate_codes(conn, args.count, args.uses, args.days)
        elif args.command == "list":
            list_codes(conn)
        elif args.command == "revoke":
            revoke_code(conn, args.code)


if __name__ == "__main__":
    main()
