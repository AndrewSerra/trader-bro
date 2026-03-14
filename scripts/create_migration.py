#!/usr/bin/env python3
"""Helper script to create a new numbered migration file."""

import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent / "bot" / "migrations"


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_migration.py <name>", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9_]+", name):
        print(
            f"Error: migration name must contain only lowercase letters, digits, and underscores (got: {name!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    existing = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if existing:
        last_num = max(int(p.name.split("_")[0]) for p in existing)
    else:
        last_num = 0

    next_num = f"{last_num + 1:03d}"
    filename = f"{next_num}_{name}.sql"
    path = MIGRATIONS_DIR / filename
    path.touch()
    print(f"Created: bot/migrations/{filename}")


if __name__ == "__main__":
    main()
