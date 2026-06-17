"""Create the local SQLite database for LineHelper Memory Store."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"

sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.memory.schema import MEMORY_SCHEMA_SQL  # noqa: E402


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(MEMORY_SCHEMA_SQL)
        connection.commit()

    print("Memory DB initialized: data/memory/linehelper_memory.db")


if __name__ == "__main__":
    main()
