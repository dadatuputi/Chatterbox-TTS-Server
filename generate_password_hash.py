#!/usr/bin/env python3
# File: generate_password_hash.py
# Helper for Feature B (HTTP auth): generate a bcrypt hash for config.yaml's
# server.auth_password_hash, and optionally a random bearer api_token.
#
# Usage:
#   python generate_password_hash.py                 # prompt for a password
#   python generate_password_hash.py "my password"   # hash the given password
#   python generate_password_hash.py --token         # also print a random api_token
#
# Paste the printed hash into config.yaml:
#   server:
#     use_auth: true
#     auth_username: "youruser"
#     auth_password_hash: "<paste here>"
#     auth_password: ""        # leave empty when using a hash
#     api_token: "<optional bearer token for /v1/audio/*>"

import getpass
import secrets
import sys


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--token"]
    want_token = "--token" in sys.argv[1:]

    try:
        import bcrypt
    except ImportError:
        print(
            "ERROR: bcrypt is not installed. Install it with:\n"
            "    pip install bcrypt\n"
            "Alternatively, use the plaintext 'auth_password' field in config.yaml "
            "(less secure).",
            file=sys.stderr,
        )
        return 1

    if args:
        password = args[0]
    else:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm : ")
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            return 1

    if not password:
        print("Empty password refused.", file=sys.stderr)
        return 1

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print("\n# --- config.yaml ---")
    print(f"auth_password_hash: \"{hashed}\"")
    if want_token:
        print(f"api_token: \"{secrets.token_urlsafe(32)}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
