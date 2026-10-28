
from app.indexing.symbol_index import index_source


def test_python_functions_classes_imports_and_calls():
    source = """
import os

class Crawler:
    def fetch(self):
        return os.getcwd()

def run():
    crawler = Crawler()
    crawler.fetch()
"""

    result = index_source(source, "python", "crawler.py")

    symbol_names = {s["name"] for s in result["symbols"]}
    imported = [item["text"] for item in result["imports"]]
    called = {item["name"] for item in result["calls"]}

    assert "Crawler" in symbol_names
    assert "fetch" in symbol_names
    assert "run" in symbol_names
    assert "import os" in imported
    assert "Crawler" in called
    assert "crawler.fetch" in called


def test_javascript_function_and_call():
    source = """
function fetchData() {
    return loadData();
}
"""

    result = index_source(source, "javascript", "app.js")

    symbol_names = {s["name"] for s in result["symbols"]}
    called = {item["name"] for item in result["calls"]}

    assert "fetchData" in symbol_names
    assert "loadData" in called


def test_python_syntax_error_is_reported():
    result = index_source(
        "def broken(:\\n    pass",
        "python",
        "broken.py",
    )

    assert result["errors"]
    assert result["symbols"] == []
