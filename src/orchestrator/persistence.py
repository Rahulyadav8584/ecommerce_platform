"""State persistence checkpointer factory for ShopHub Orchestrator (R7).

Supports:
- SQLite persistence (default for offline execution, zero setup, durable across restarts)
- PostgreSQL persistence (via PostgresSaver when DATABASE_URL is configured)
"""

import os
import sqlite3
from typing import Any, Optional
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

DEFAULT_SQLITE_PATH = os.path.join("data", "chat_checkpoints.sqlite")


def get_checkpointer(db_url: Optional[str] = None) -> BaseCheckpointSaver:
    """Returns a durable checkpointer so conversation state survives restarts."""
    url = db_url or os.getenv("DATABASE_URL", "")

    if url.startswith("postgres://") or url.startswith("postgresql://"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            # Return PostgresSaver from connection string
            conn = Connection.connect(url, autocommit=True)
            saver = PostgresSaver(conn)
            saver.setup()
            return saver
        except Exception as e:
            # Fall back gracefully to SQLite if postgres connection fails
            print(f"[Persistence] Failed to connect to Postgres ({e}), falling back to SQLite.")

    # SQLite persistence (Default)
    sqlite_file = os.getenv("SQLITE_DB_PATH", DEFAULT_SQLITE_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(sqlite_file)), exist_ok=True)

    conn = sqlite3.connect(sqlite_file, check_same_thread=False)
    saver = SqliteSaver(conn)
    return saver

