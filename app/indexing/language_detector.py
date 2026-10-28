from html.parser import HTMLParser
from pathlib import Path


EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".sql": "sql",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}


class EmbeddedCodeExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.regions = []
        self.current_language = None
        self.current_start_line = None
        self.current_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "script" and not attrs.get("src"):
            script_type = attrs.get("type", "").lower()
            if script_type in ("", "text/javascript", "module",
                               "application/javascript"):
                self._start_region("javascript")

        elif tag == "style":
            self._start_region("css")

    def _start_region(self, language):
        self.current_language = language
        self.current_start_line = self.getpos()[0]
        self.current_parts = []

    def handle_data(self, data):
        if self.current_language:
            self.current_parts.append(data)

    def handle_endtag(self, tag):
        expected = {
            "script": "javascript",
            "style": "css",
        }.get(tag)

        if expected and self.current_language == expected:
            self.regions.append({
                "language": self.current_language,
                "start_line": self.current_start_line,
                "source": "".join(self.current_parts),
            })
            self.current_language = None
            self.current_start_line = None
            self.current_parts = []


def detect_file(path: Path) -> dict:
    language = EXTENSION_LANGUAGES.get(path.suffix.lower())

    result = {
        "file": str(path),
        "language": language or "unknown",
        "embedded_regions": [],
    }

    if language != "html":
        return result

    try:
        content = path.read_text(encoding="utf-8")
        extractor = EmbeddedCodeExtractor()
        extractor.feed(content)
        extractor.close()
        result["embedded_regions"] = extractor.regions
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        result["error"] = str(exc)

    return result