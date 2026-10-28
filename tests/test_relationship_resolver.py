import sqlite3
from pathlib import Path

from app.indexing.repository_indexer import index_repository
from app.indexing.sqlite_store import SQLiteStore
from app.indexing.relationship_resolver import RelationshipResolver


def test_import_resolution_absolute_relative_aliased_and_package_init(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    pkg_dir = project_dir / "pkg"
    pkg_dir.mkdir()

    (pkg_dir / "__init__.py").write_text("# package root\n", encoding="utf-8")
    (pkg_dir / "module_a.py").write_text(
        "def helper():\n    return 42\n", encoding="utf-8"
    )
    (pkg_dir / "module_b.py").write_text(
        "import os\n"
        "from . import module_a\n"
        "from .module_a import helper as aliased_helper\n"
        "from pkg import module_a as ma\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"
    result = index_repository(project_dir, db_path)
    assert result["indexed_files"] == 3

    store = SQLiteStore(db_path)
    try:
        rels = store.get_relationships(source_file="pkg/module_b.py", type="imports")

        # 1. External import (os)
        os_rel = next((r for r in rels if r["target_symbol"] == "os"), None)
        assert os_rel is not None
        assert os_rel["target_file"] is None
        assert os_rel["status"] == "unresolved"

        # 2. Relative package submodule import (. import module_a)
        sub_rel = next((r for r in rels if r["target_file"] == "pkg/module_a.py" and r["target_symbol"] == "module_a"), None)
        assert sub_rel is not None
        assert sub_rel["status"] == "resolved"

        # 3. Relative symbol import (.module_a import helper as aliased_helper)
        helper_rel = next((r for r in rels if r["target_file"] == "pkg/module_a.py" and r["target_symbol"] == "helper"), None)
        assert helper_rel is not None
        assert helper_rel["status"] == "resolved"
        assert helper_rel["target_symbol_id"] is not None

        # 4. Absolute package import (from pkg import module_a as ma)
        ma_rel = next((r for r in rels if r["target_file"] == "pkg/module_a.py" and r["target_symbol"] == "module_a"), None)
        assert ma_rel is not None
        assert ma_rel["status"] == "resolved"
    finally:
        store.close()


def test_calls_same_file_imported_and_unresolved(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (project_dir / "main.py").write_text(
        "from math_utils import add\n"
        "import os\n"
        "\n"
        "def local_fn():\n"
        "    return 10\n"
        "\n"
        "def compute():\n"
        "    x = add(1, 2)\n"
        "    y = local_fn()\n"
        "    z = unknown_external_fn()\n"
        "    os.getcwd()\n"
        "    return x + y\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"
    index_repository(project_dir, db_path)

    store = SQLiteStore(db_path)
    try:
        calls = store.get_relationships(source_file="main.py", type="calls")

        # Imported call: add
        add_call = next((c for c in calls if c["target_symbol"] == "add"), None)
        assert add_call is not None
        assert add_call["target_file"] == "math_utils.py"
        assert add_call["status"] == "resolved"
        assert add_call["target_symbol_id"] is not None

        # Same-file call: local_fn
        local_call = next((c for c in calls if c["target_symbol"] == "local_fn"), None)
        assert local_call is not None
        assert local_call["target_file"] == "main.py"
        assert local_call["status"] == "resolved"
        assert local_call["target_symbol_id"] is not None

        # Unresolved call: unknown_external_fn
        unknown_call = next((c for c in calls if c["target_symbol"] == "unknown_external_fn"), None)
        assert unknown_call is not None
        assert unknown_call["target_file"] is None
        assert unknown_call["status"] == "unresolved"

        # Unresolved external dotted call: os.getcwd
        os_call = next((c for c in calls if c["target_symbol"] == "os.getcwd"), None)
        assert os_call is not None
        assert os_call["target_file"] is None
        assert os_call["status"] == "unresolved"
    finally:
        store.close()


def test_self_and_cls_method_calls_and_defines_method(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "worker.py").write_text(
        "class Worker:\n"
        "    def step(self):\n"
        "        return 1\n"
        "\n"
        "    def process(self):\n"
        "        return self.step()\n"
        "\n"
        "    @classmethod\n"
        "    def do_work(cls):\n"
        "        return 2\n"
        "\n"
        "    @classmethod\n"
        "    def run(cls):\n"
        "        return cls.do_work()\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"
    index_repository(project_dir, db_path)

    store = SQLiteStore(db_path)
    try:
        # Check defines_method
        method_defs = store.get_relationships(source_file="worker.py", type="defines_method")
        assert len(method_defs) == 4
        assert {m["target_symbol"] for m in method_defs} == {"step", "process", "do_work", "run"}
        assert all(m["source_symbol"] == "Worker" for m in method_defs)
        assert all(m["status"] == "resolved" for m in method_defs)

        # Check self.step() call
        calls = store.get_relationships(source_file="worker.py", type="calls")
        self_call = next((c for c in calls if "step" in (c["target_symbol"] or "")), None)
        assert self_call is not None
        assert self_call["target_file"] == "worker.py"
        assert self_call["target_symbol"] == "Worker.step"
        assert self_call["source_symbol"] == "Worker.process"
        assert self_call["status"] == "resolved"

        # Check cls.do_work() call
        cls_call = next((c for c in calls if "do_work" in (c["target_symbol"] or "")), None)
        assert cls_call is not None
        assert cls_call["target_file"] == "worker.py"
        assert cls_call["target_symbol"] == "Worker.do_work"
        assert cls_call["source_symbol"] == "Worker.run"
        assert cls_call["status"] == "resolved"
    finally:
        store.close()


def test_inheritance_resolution_local_imported_and_unresolved(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "base.py").write_text(
        "class BaseWorker:\n    pass\n", encoding="utf-8"
    )
    (project_dir / "derived.py").write_text(
        "from base import BaseWorker\n"
        "\n"
        "class LocalBase:\n"
        "    pass\n"
        "\n"
        "class CustomWorker(BaseWorker, LocalBase, ExternalBase):\n"
        "    pass\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"
    index_repository(project_dir, db_path)

    store = SQLiteStore(db_path)
    try:
        inherits = store.get_relationships(source_file="derived.py", type="inherits")
        assert len(inherits) == 3

        # BaseWorker: resolved from base.py
        base_rel = next((r for r in inherits if r["target_symbol"] == "BaseWorker"), None)
        assert base_rel is not None
        assert base_rel["target_file"] == "base.py"
        assert base_rel["status"] == "resolved"

        # LocalBase: resolved from derived.py
        local_rel = next((r for r in inherits if r["target_symbol"] == "LocalBase"), None)
        assert local_rel is not None
        assert local_rel["target_file"] == "derived.py"
        assert local_rel["status"] == "resolved"

        # ExternalBase: unresolved
        ext_rel = next((r for r in inherits if r["target_symbol"] == "ExternalBase"), None)
        assert ext_rel is not None
        assert ext_rel["target_file"] is None
        assert ext_rel["status"] == "unresolved"
    finally:
        store.close()


def test_ambiguous_method_calls_preserve_candidates(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "worker_a.py").write_text(
        "class WorkerA:\n    def execute(self):\n        return 'A'\n", encoding="utf-8"
    )
    (project_dir / "worker_b.py").write_text(
        "class WorkerB:\n    def execute(self):\n        return 'B'\n", encoding="utf-8"
    )
    (project_dir / "runner.py").write_text(
        "def run_worker(worker):\n    worker.execute()\n", encoding="utf-8"
    )

    db_path = tmp_path / "index.sqlite3"
    index_repository(project_dir, db_path)

    store = SQLiteStore(db_path)
    try:
        calls = store.get_relationships(source_file="runner.py", type="calls")
        ambig = [c for c in calls if c["status"] == "ambiguous"]
        assert len(ambig) == 2
        target_symbols = {c["target_symbol"] for c in ambig}
        assert "WorkerA.execute" in target_symbols
        assert "WorkerB.execute" in target_symbols
    finally:
        store.close()


def test_incremental_indexing_and_file_deletion(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    b_file = project_dir / "b.py"
    b_file.write_text("def target_func():\n    return 1\n", encoding="utf-8")

    a_file = project_dir / "a.py"
    a_file.write_text(
        "from b import target_func\n"
        "def main():\n"
        "    return target_func()\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"

    # Initial index
    res1 = index_repository(project_dir, db_path)
    assert res1["indexed_files"] == 2

    store = SQLiteStore(db_path)
    try:
        call_rel = store.get_relationships(source_file="a.py", type="calls")
        assert len(call_rel) == 1
        assert call_rel[0]["target_symbol"] == "target_func"
        assert call_rel[0]["status"] == "resolved"
    finally:
        store.close()

    # Modify a.py by adding a new call
    a_file.write_text(
        "from b import target_func\n"
        "def helper():\n    return 0\n"
        "def main():\n"
        "    helper()\n"
        "    return target_func()\n",
        encoding="utf-8",
    )

    res2 = index_repository(project_dir, db_path)
    assert res2["indexed_files"] == 1
    assert res2["unchanged_files_skipped"] == 1

    store = SQLiteStore(db_path)
    try:
        call_rels = store.get_relationships(source_file="a.py", type="calls")
        assert len(call_rels) == 2
        symbols = {c["target_symbol"] for c in call_rels}
        assert "target_func" in symbols
        assert "helper" in symbols
    finally:
        store.close()

    # Delete b.py
    b_file.unlink()

    res3 = index_repository(project_dir, db_path)
    assert res3["removed_files"] == 1

    store = SQLiteStore(db_path)
    try:
        # Relationships targeting b.py should no longer exist
        b_rels = store.get_relationships(target_file="b.py")
        assert len(b_rels) == 0
    finally:
        store.close()


def test_repeated_indexing_is_idempotent_no_duplicates(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "app.py").write_text(
        "def foo():\n    return 1\n\ndef bar():\n    return foo()\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "index.sqlite3"

    res1 = index_repository(project_dir, db_path)
    assert res1["indexed_files"] == 1
    rel_count1 = res1["counts"]["relationships"]
    assert rel_count1 > 0

    # Index again with no changes
    res2 = index_repository(project_dir, db_path)
    assert res2["indexed_files"] == 0
    assert res2["unchanged_files_skipped"] == 1
    rel_count2 = res2["counts"]["relationships"]

    assert rel_count1 == rel_count2


def test_existing_database_backward_compatibility(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite3"

    # Create a database with the old schema (without relationships table)
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE files (
            path TEXT PRIMARY KEY,
            language TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    con.execute("""
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            language TEXT NOT NULL,
            line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            scope TEXT NOT NULL DEFAULT ''
        );
    """)
    con.execute("""
        CREATE TABLE imports (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            text TEXT NOT NULL,
            language TEXT NOT NULL,
            line INTEGER NOT NULL
        );
    """)
    con.execute("""
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            language TEXT NOT NULL,
            line INTEGER NOT NULL
        );
    """)
    con.execute("INSERT INTO files VALUES ('main.py', 'python', 'hash123', 50, CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO symbols VALUES (1, 'main.py', 'run', 'function', 'python', 1, 3, '')")
    con.commit()
    con.close()

    # Opening with SQLiteStore must succeed and auto-create relationships table
    store = SQLiteStore(db_path)
    try:
        counts = store.counts()
        assert counts["files"] == 1
        assert counts["symbols"] == 1
        assert counts["relationships"] == 0

        # Query relationships
        rels = store.get_relationships()
        assert rels == []
    finally:
        store.close()


def test_fallback_language_adapter_safety(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    (project_dir / "app.js").write_text(
        "function run() {\n    loadData();\n}\n", encoding="utf-8"
    )

    db_path = tmp_path / "index.sqlite3"
    index_repository(project_dir, db_path)

    store = SQLiteStore(db_path)
    try:
        calls = store.get_relationships(source_file="app.js", type="calls")
        assert len(calls) == 1
        assert calls[0]["target_symbol"] == "loadData"
        # JavaScript call is marked unresolved rather than fabricating semantic resolution
        assert calls[0]["status"] == "unresolved"
        assert calls[0]["target_file"] is None
    finally:
        store.close()
