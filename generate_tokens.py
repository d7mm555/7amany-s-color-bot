"""Print random redemption tokens to paste into the Tokens sheet (column A).

Usage: py generate_tokens.py [count]
"""

import secrets
import string
import sys

ALPHABET = string.ascii_uppercase + string.digits


def make_token(length=10):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    for _ in range(count):
        print(make_token())
