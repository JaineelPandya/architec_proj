import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.indexing.repository_indexer import index_repository
from app.indexing.sqlite_store import SQLiteStore
from app.api.intelligence import get_store


@pytest.fixture
def test_project(tmp_path: Path):
    """Create and index a test fixture repository."""
    project_dir = tmp_path / "fixture_repo"
    project_dir.mkdir()
    pkg_dir = project_dir / "pkg"
    pkg_dir.mkdir()

    (pkg_dir / "__init__.py").write_text("# package\n", encoding="utf-8")
    (pkg_dir / "utils.py").write_text(
        "def helper():\n"
        "    return 'help'\n"
        "\n"
        "def calculate():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (pkg_dir / "models.py").write_text(
        "class BaseModel:\n"
        "    def save(self):\n"
        "        return True\n"
        "\n"
        "class User(BaseModel):\n"
        "    def save(self):\n"
        "        return self.validate()\n"
        "\n"
        "    def validate(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    (pkg_dir / "another.py").write_text(
        "class Document:\n"
        "    def save(self):\n"
        "        return False\n",
        encoding="utf-8",
    )
    (pkg_dir / "service.py").write_text(
        "import os\n"
        "from pkg.utils import helper\n"
        "from pkg.models import User\n"
        "\n"
        "def run_pipeline():\n"
        "    h = helper()\n"
        "    u = User()\n"
        "    u.save()\n"
        "    os.getcwd()\n"
        "    unknown_call()\n"
        "    return h\n"
        "\n"
        "def dispatch(handler):\n"
        "    handler.save()\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "test_index.sqlite3"
    index_repository(project_dir, db_path)
    return db_path


@pytest.fixture
def client(test_project: Path):
    """TestClient with overridden get_store dependency."""
    def override_get_store():
        store = SQLiteStore(test_project)
        try:
            yield store
        finally:
            store.close()

    app.dependency_overrides[get_store] = override_get_store
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_get_index_stats(client: TestClient):
    response = client.get("/api/v1/index/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["files"] == 5
    assert data["symbols"] > 0
    assert data["imports"] > 0
    assert data["calls"] > 0
    assert data["relationships"] > 0


def test_symbol_search(client: TestClient):
    # Search class
    res = client.get("/api/v1/symbols/search?q=User")
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "User"
    assert data["count"] >= 1
    assert any(s["name"] == "User" and s["type"] == "class" for s in data["symbols"])

    # Search with type filter
    res_methods = client.get("/api/v1/symbols/search?q=save&type=method")
    assert res_methods.status_code == 200
    methods_data = res_methods.json()
    assert methods_data["count"] >= 2
    assert all(s["type"] == "method" for s in methods_data["symbols"])

    # Empty query validation
    res_empty = client.get("/api/v1/symbols/search?q=   ")
    assert res_empty.status_code == 400
    assert "must not be empty" in res_empty.json()["detail"]

    # Non-existent symbol
    res_none = client.get("/api/v1/symbols/search?q=NonExistent12345")
    assert res_none.status_code == 200
    assert res_none.json()["count"] == 0
    assert res_none.json()["symbols"] == []


def test_file_dependencies_and_missing_file(client: TestClient):
    # Existing file
    res = client.get("/api/v1/files/pkg/service.py/dependencies")
    assert res.status_code == 200
    data = res.json()
    assert data["file"] == "pkg/service.py"
    assert "pkg/utils.py" in data["resolved_files"]
    assert "pkg/models.py" in data["resolved_files"]
    assert data["unresolved_count"] > 0
    assert data["total_dependencies"] > 0

    # Ensure unresolved references are not marked as resolved
    unresolved_deps = [d for d in data["dependencies"] if d["status"] == "unresolved"]
    assert any(d["target_symbol"] == "os" for d in unresolved_deps)
    assert all(d["target_file"] is None for d in unresolved_deps)

    # Missing file returns 404
    res_404 = client.get("/api/v1/files/nonexistent/file.py/dependencies")
    assert res_404.status_code == 404
    assert "not found in index" in res_404.json()["detail"].lower()


def test_file_dependents_and_missing_file(client: TestClient):
    # Dependents of pkg/utils.py
    res = client.get("/api/v1/files/pkg/utils.py/dependents")
    assert res.status_code == 200
    data = res.json()
    assert data["file"] == "pkg/utils.py"
    assert "pkg/service.py" in data["dependent_files"]
    assert data["total_dependents"] > 0

    # Dependents of pkg/models.py
    res_models = client.get("/api/v1/files/pkg/models.py/dependents")
    assert res_models.status_code == 200
    assert "pkg/service.py" in res_models.json()["dependent_files"]

    # Missing file returns 404
    res_404 = client.get("/api/v1/files/nonexistent/file.py/dependents")
    assert res_404.status_code == 404


def test_symbol_callers(client: TestClient):
    # Callers of helper
    res = client.get("/api/v1/symbols/callers?name=helper")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 2
    sources = {c["source_file"] for c in data["callers"]}
    assert "pkg/utils.py" in sources
    assert "pkg/service.py" in sources

    # Callers of validate
    res_val = client.get("/api/v1/symbols/callers?name=validate")
    assert res_val.status_code == 200
    val_data = res_val.json()
    assert val_data["count"] == 1
    assert val_data["callers"][0]["source_symbol"] == "User.save"

    # Empty name validation
    res_empty = client.get("/api/v1/symbols/callers?name=   ")
    assert res_empty.status_code == 400

    # Unused symbol
    res_unused = client.get("/api/v1/symbols/callers?name=uncalled_func")
    assert res_unused.status_code == 200
    assert res_unused.json()["count"] == 0
    assert res_unused.json()["callers"] == []


def test_symbol_callees(client: TestClient):
    # Callees of calculate
    res = client.get("/api/v1/symbols/callees?name=calculate")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 1
    assert any(c["target_symbol"] == "helper" for c in data["callees"])

    # Callees of User.save
    res_save = client.get("/api/v1/symbols/callees?name=User.save")
    assert res_save.status_code == 200
    assert any("validate" in c["target_symbol"] for c in res_save.json()["callees"])

    # Empty name validation
    res_empty = client.get("/api/v1/symbols/callees?name=  ")
    assert res_empty.status_code == 400

    # Unknown symbol
    res_unknown = client.get("/api/v1/symbols/callees?name=no_such_function")
    assert res_unknown.status_code == 200
    assert res_unknown.json()["count"] == 0


def test_relationships_filter_and_pagination(client: TestClient):
    # Filter by type: imports
    res_imports = client.get("/api/v1/relationships?type=imports")
    assert res_imports.status_code == 200
    imp_data = res_imports.json()
    assert imp_data["total"] > 0
    assert all(r["type"] == "imports" for r in imp_data["relationships"])

    # Filter by status: resolved
    res_resolved = client.get("/api/v1/relationships?status=resolved")
    assert res_resolved.status_code == 200
    res_data = res_resolved.json()
    assert all(r["status"] == "resolved" for r in res_data["relationships"])

    # Filter by status: ambiguous
    res_ambig = client.get("/api/v1/relationships?status=ambiguous")
    assert res_ambig.status_code == 200
    ambig_data = res_ambig.json()
    assert ambig_data["total"] > 0
    assert all(r["status"] == "ambiguous" for r in ambig_data["relationships"])

    # Filter by source_file
    res_src = client.get("/api/v1/relationships?source_file=pkg/service.py&limit=5&offset=0")
    assert res_src.status_code == 200
    src_data = res_src.json()
    assert src_data["limit"] == 5
    assert src_data["offset"] == 0
    assert len(src_data["relationships"]) <= 5
    assert all(r["source_file"] == "pkg/service.py" for r in src_data["relationships"])

    # Invalid type
    res_bad_type = client.get("/api/v1/relationships?type=invalid_type")
    assert res_bad_type.status_code == 400
    assert "Invalid type" in res_bad_type.json()["detail"]

    # Invalid status
    res_bad_status = client.get("/api/v1/relationships?status=invalid_status")
    assert res_bad_status.status_code == 400
    assert "Invalid status" in res_bad_status.json()["detail"]

    # Invalid limit / offset
    res_bad_limit = client.get("/api/v1/relationships?limit=0")
    assert res_bad_limit.status_code == 422 or res_bad_limit.status_code == 400

    res_bad_offset = client.get("/api/v1/relationships?offset=-1")
    assert res_bad_offset.status_code == 422 or res_bad_offset.status_code == 400


def test_empty_database_handling(tmp_path: Path):
    empty_db = tmp_path / "empty.sqlite3"
    SQLiteStore(empty_db).close()

    def override():
        store = SQLiteStore(empty_db)
        try:
            yield store
        finally:
            store.close()

    app.dependency_overrides[get_store] = override
    empty_client = TestClient(app)

    # Stats on empty database
    res = empty_client.get("/api/v1/index/stats")
    assert res.status_code == 200
    assert res.json()["files"] == 0
    assert res.json()["relationships"] == 0

    # Search on empty database
    res_search = empty_client.get("/api/v1/symbols/search?q=test")
    assert res_search.status_code == 200
    assert res_search.json()["count"] == 0

    # Dependencies on missing file
    res_dep = empty_client.get("/api/v1/files/some_file.py/dependencies")
    assert res_dep.status_code == 404

    app.dependency_overrides.clear()
