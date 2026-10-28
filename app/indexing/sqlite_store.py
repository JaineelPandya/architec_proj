
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    language TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    scope TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_symbols_name
    ON symbols(name);

CREATE INDEX IF NOT EXISTS idx_symbols_file
    ON symbols(file_path);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_imports_file
    ON imports(file_path);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_name
    ON calls(name);

CREATE INDEX IF NOT EXISTS idx_calls_file
    ON calls(file_path);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    source_symbol TEXT,
    target_file TEXT REFERENCES files(path) ON DELETE CASCADE,
    target_symbol TEXT,
    target_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    line INTEGER NOT NULL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relationships_source_file
    ON relationships(source_file);

CREATE INDEX IF NOT EXISTS idx_relationships_target_file
    ON relationships(target_file);

CREATE INDEX IF NOT EXISTS idx_relationships_type
    ON relationships(type);

CREATE INDEX IF NOT EXISTS idx_relationships_status
    ON relationships(status);

CREATE INDEX IF NOT EXISTS idx_relationships_target_symbol_id
    ON relationships(target_symbol_id);

CREATE INDEX IF NOT EXISTS idx_relationships_target_symbol
    ON relationships(target_symbol);

CREATE INDEX IF NOT EXISTS idx_relationships_source_symbol
    ON relationships(source_symbol);

CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_unique
    ON relationships(
        source_file,
        line,
        type,
        COALESCE(source_symbol, ''),
        COALESCE(target_file, ''),
        COALESCE(target_symbol, ''),
        COALESCE(target_symbol_id, -1)
    );
"""


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def get_file_hash(self, path: str) -> str | None:
        row = self.connection.execute(
            "SELECT sha256 FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row["sha256"] if row else None

    def replace_file(
        self,
        path: str,
        language: str,
        sha256: str,
        size_bytes: int,
        result: dict,
    ) -> None:
        """Atomically replace one file and all of its indexed records."""
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO files (path, language, sha256, size_bytes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language = excluded.language,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    indexed_at = CURRENT_TIMESTAMP
                """,
                (path, language, sha256, size_bytes),
            )

            for table in ("symbols", "imports", "calls"):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE file_path = ?", (path,)
                )
            self.connection.execute(
                "DELETE FROM relationships WHERE source_file = ?", (path,)
            )

            self.connection.executemany(
                """
                INSERT INTO symbols
                (file_path, name, type, language, line, end_line, scope)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        path,
                        item["name"],
                        item["type"],
                        item["language"],
                        item["line"],
                        item.get("end_line", item["line"]),
                        item.get("scope", ""),
                    )
                    for item in result.get("symbols", [])
                ],
            )

            self.connection.executemany(
                """
                INSERT INTO imports (file_path, text, language, line)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (path, item["text"], item["language"], item["line"])
                    for item in result.get("imports", [])
                ],
            )

            self.connection.executemany(
                """
                INSERT INTO calls (file_path, name, language, line)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (path, item["name"], item["language"], item["line"])
                    for item in result.get("calls", [])
                ],
            )

    def remove_missing_files(self, current_paths: set[str]) -> int:
        """Remove indexed files that no longer exist in the project."""
        rows = self.connection.execute("SELECT path FROM files").fetchall()
        stale = [row["path"] for row in rows if row["path"] not in current_paths]

        with self.connection:
            for path in stale:
                self.connection.execute(
                    "DELETE FROM relationships WHERE source_file = ? OR target_file = ?",
                    (path, path),
                )
            self.connection.executemany(
                "DELETE FROM files WHERE path = ?", [(path,) for path in stale]
            )

        return len(stale)

    def store_relationships(self, relationships: list[dict]) -> None:
        """Atomically insert or ignore relationship records."""
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO relationships
                (source_file, source_symbol, target_file, target_symbol, target_symbol_id, type, line, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["source_file"],
                        r.get("source_symbol"),
                        r.get("target_file"),
                        r.get("target_symbol"),
                        r.get("target_symbol_id"),
                        r["type"],
                        r["line"],
                        r["status"],
                    )
                    for r in relationships
                ],
            )

    def delete_relationships_for_files(self, file_paths: set[str] | list[str]) -> None:
        """Delete all relationships originating from the specified source files."""
        with self.connection:
            self.connection.executemany(
                "DELETE FROM relationships WHERE source_file = ?",
                [(p,) for p in file_paths],
            )

    def get_relationships(
        self,
        source_file: str | None = None,
        target_file: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Query relationships with optional filters."""
        query = (
            "SELECT id, source_file, source_symbol, target_file, "
            "target_symbol, target_symbol_id, type, line, status "
            "FROM relationships WHERE 1=1"
        )
        params: list[Any] = []
        if source_file is not None:
            query += " AND source_file = ?"
            params.append(source_file)
        if target_file is not None:
            query += " AND target_file = ?"
            params.append(target_file)
        if type is not None:
            query += " AND type = ?"
            params.append(type)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY source_file, line, id"
        rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def file_exists(self, path: str) -> bool:
        """Check if a file path is recorded in the index."""
        row = self.connection.execute(
            "SELECT 1 FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row is not None

    def get_file_dependencies(self, path: str) -> list[dict]:
        """Get all relationships originating from the specified source file."""
        rows = self.connection.execute(
            """
            SELECT id, source_file, source_symbol, target_file, target_symbol,
                   target_symbol_id, type, line, status
            FROM relationships
            WHERE source_file = ?
            ORDER BY line, id
            """,
            (path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_file_dependents(self, path: str) -> list[dict]:
        """Get all relationships targeting the specified file."""
        rows = self.connection.execute(
            """
            SELECT id, source_file, source_symbol, target_file, target_symbol,
                   target_symbol_id, type, line, status
            FROM relationships
            WHERE target_file = ?
            ORDER BY source_file, line, id
            """,
            (path,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_symbol_callers(
        self,
        name: str,
        file_path: str | None = None,
    ) -> list[dict]:
        """Find calls where the target symbol matches the specified name."""
        query = """
            SELECT id, source_file, source_symbol, target_file, target_symbol,
                   target_symbol_id, type, line, status
            FROM relationships
            WHERE type = 'calls'
              AND (target_symbol = ? OR target_symbol LIKE ?)
        """
        params: list[Any] = [name, f"%.{name}"]
        if file_path:
            query += " AND (target_file = ? OR target_file IS NULL)"
            params.append(file_path)
        query += " ORDER BY source_file, line, id"
        rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_symbol_callees(
        self,
        name: str,
        file_path: str | None = None,
    ) -> list[dict]:
        """Find calls made from within the specified symbol scope."""
        query = """
            SELECT id, source_file, source_symbol, target_file, target_symbol,
                   target_symbol_id, type, line, status
            FROM relationships
            WHERE type = 'calls'
              AND (source_symbol = ? OR source_symbol LIKE ?)
        """
        params: list[Any] = [name, f"%.{name}"]
        if file_path:
            query += " AND source_file = ?"
            params.append(file_path)
        query += " ORDER BY source_file, line, id"
        rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_filtered_relationships(
        self,
        source_file: str | None = None,
        target_file: str | None = None,
        type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        """Query relationships with filters and pagination, returning (total_count, records)."""
        where_clause = " WHERE 1=1"
        params: list[Any] = []
        if source_file is not None:
            where_clause += " AND source_file = ?"
            params.append(source_file)
        if target_file is not None:
            where_clause += " AND target_file = ?"
            params.append(target_file)
        if type is not None:
            where_clause += " AND type = ?"
            params.append(type)
        if status is not None:
            where_clause += " AND status = ?"
            params.append(status)

        count_sql = f"SELECT COUNT(*) FROM relationships{where_clause}"
        total = self.connection.execute(count_sql, params).fetchone()[0]

        data_sql = (
            f"SELECT id, source_file, source_symbol, target_file, target_symbol, "
            f"target_symbol_id, type, line, status "
            f"FROM relationships{where_clause} "
            f"ORDER BY source_file, line, id LIMIT ? OFFSET ?"
        )
        rows = self.connection.execute(data_sql, params + [limit, offset]).fetchall()
        return total, [dict(row) for row in rows]

    def search_symbols(
        self,
        name: str,
        symbol_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        query = """
            SELECT id, name, type, language, file_path, line, end_line, scope
            FROM symbols
            WHERE name LIKE ? ESCAPE '\\'
        """
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params: list[Any] = [f"%{escaped}%"]
        if symbol_type:
            query += " AND type = ?"
            params.append(symbol_type)
        query += " ORDER BY file_path, line LIMIT ?"
        params.append(max(1, min(limit, 500)))
        rows = self.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict:
        return {
            table: self.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in ("files", "symbols", "imports", "calls", "relationships")
        }

    def close(self) -> None:
        self.connection.close()
