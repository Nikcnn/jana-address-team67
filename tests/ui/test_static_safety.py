"""Offline/XSS/locked-field invariants; no frontend dependencies required."""
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
FIELDS = (
    "id", "raw_address", "province", "city", "district", "living_area",
    "street_type", "street_name", "house", "apartment", "entrance", "floor",
    "landmark", "confidence",
)


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_no_external_urls_or_html_execution_sinks():
    for path in STATIC.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"https?://", text, re.I), path
        assert not re.search(r"\b(innerHTML|outerHTML|insertAdjacentHTML|eval)\b|document\.write", text), path
        assert not re.search(r"@import|url\(\s*['\"]?//", text, re.I), path
    elements = Elements((STATIC / "index.html").read_text()).elements
    for _, attrs in elements:
        assert not any(key.startswith("on") for key in attrs), attrs
        for key in ("src", "href", "action"):
            if key in attrs:
                assert attrs[key].startswith(("/", "#")) and not attrs[key].startswith("//"), attrs


def test_identity_fields_are_explicitly_readonly():
    elements = Elements((STATIC / "index.html").read_text()).elements
    for identifier in ("edit_id", "edit_raw_address"):
        matches = [(tag, attrs) for tag, attrs in elements if attrs.get("id") == identifier]
        assert len(matches) == 1, identifier
        tag, attrs = matches[0]
        assert tag in ("input", "textarea")
        assert "readonly" in attrs or "disabled" in attrs


def test_all_fourteen_fields_and_both_exports_are_available():
    text = (STATIC / "index.html").read_text()
    elements = Elements(text).elements
    assert tuple(attrs["data-field"] for tag, attrs in elements
                 if tag == "th" and "data-field" in attrs) == FIELDS
    ids = {attrs.get("id") for _, attrs in elements}
    assert {"exportBtn", "exportCsvBtn", "confidenceThreshold"} <= ids
    assert "эвристика, не точность" in text.lower()
