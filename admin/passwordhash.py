"""CLI helper: print an argon2 hash for the ADMIN_PASSWORD_HASH env var.

Usage (from admin/):
    uv run python -m passwordhash <password>      # password as argv (visible in shell history)
    uv run python -m passwordhash                 # prompts hidden via getpass
"""

import getpass
import sys

from argon2 import PasswordHasher


def main() -> None:
    if len(sys.argv) > 2:
        sys.stderr.write("Usage: python -m passwordhash [<password>]\n")
        sys.exit(2)
    password = sys.argv[1] if len(sys.argv) == 2 else getpass.getpass("Password: ")
    if not password:
        sys.stderr.write("Password cannot be empty.\n")
        sys.exit(2)
    print(PasswordHasher().hash(password))


if __name__ == "__main__":
    main()
