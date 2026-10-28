from pathlib import Path

from app.indexing.language_detector import detect_file


def test_detects_javascript_and_css_inside_html():
    path = Path("tests/fixtures/dashboard.html")

    result = detect_file(path)

    assert result["language"] == "html"

    languages = {
        region["language"]
        for region in result["embedded_regions"]
    }

    assert "javascript" in languages
    assert "css" in languages