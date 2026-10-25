"""Grant test credits to one account. Run by hand in the Railway Console:

    python scripts/grant_credits.py 0xYourWalletAddress 50
    python scripts/grant_credits.py you@gmail.com 50

Credits go into wallet_credits (the purchased pool, never expires).
An email must have signed in with Google once in this environment first,
because the account is created at first sign-in.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

MAX_GRANT = 10000


def resolve_account(who: str) -> str:
    who = who.strip()
    if "@" in who:
        conn = sqlite3.connect(str(db.DB_PATH))
        try:
            rows = conn.execute(
                "SELECT account_id FROM email_accounts WHERE lower(email) = ?",
                (who.lower(),),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            sys.exit(f"No account for {who}. Sign in with that Gmail on the site first, then retry.")
        if len(rows) > 1:
            sys.exit(f"More than one account matches {who}; stop and check email_accounts.")
        return rows[0][0]
    if who.lower().startswith("0x") and len(who) == 42:
        return who.lower()
    sys.exit("Give a 42-character 0x wallet address or a Gmail address.")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: python scripts/grant_credits.py <wallet-or-email> <amount>")
    try:
        amount = int(sys.argv[2])
    except ValueError:
        sys.exit("Amount must be a whole number.")
    if amount < 1 or amount > MAX_GRANT:
        sys.exit(f"Amount must be between 1 and {MAX_GRANT}.")
    if not os.path.exists(str(db.DB_PATH)):
        sys.exit(f"Database not found at {db.DB_PATH}. Run this from the service Console, not locally.")

    account = resolve_account(sys.argv[1])
    before = db.get_credit_balance(account)
    after = db.add_credits(account, amount)
    print(f"database: {db.DB_PATH}")
    print(f"account:  {account}")
    print(f"balance:  {before} -> {after}")


if __name__ == "__main__":
    main()
