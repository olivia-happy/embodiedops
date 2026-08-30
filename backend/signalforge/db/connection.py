"""Small DuckDB connection wrapper with a single local schema entry point."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb


class Database:
    """Own a local DuckDB connection used by repositories and services.

    The wrapper intentionally exposes only parameterized execution. HTTP routes
    must call repository functions rather than pass arbitrary SQL to this class.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(self.path))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> duckdb.DuckDBPyConnection:
        """Run a repository-owned statement with bound positional parameters."""

        return self.connection.execute(sql, params)

    def apply_schema(self) -> None:
        """Create all tables required by the versioned local data store."""

        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.connection.execute(schema)

    def close(self) -> None:
        """Close the underlying connection when an application lifespan ends."""

        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
