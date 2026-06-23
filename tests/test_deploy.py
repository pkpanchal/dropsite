import pytest
from fastapi import HTTPException

from app.deploy import _safe_path, _strip_common_prefix, slugify, validate_slug


def test_slugify_html():
    assert slugify("quarterly-report.html") == "quarterly-report"


def test_slugify_spaces():
    assert slugify("My Report.html") == "my-report"


def test_slugify_special_chars():
    assert slugify("2024_Q1 Report!.html") == "2024-q1-report"


def test_slugify_truncates():
    assert len(slugify("a" * 100 + ".html")) <= 40


def test_slugify_dotfile():
    # ".html" → stem is ".html" → slugified to "html" (valid, not empty)
    assert slugify(".html") == "html"


def test_validate_slug_reserved():
    with pytest.raises(HTTPException) as exc:
        validate_slug("admin")
    assert exc.value.status_code == 400


def test_validate_slug_invalid_uppercase():
    with pytest.raises(HTTPException):
        validate_slug("MySlug")


def test_validate_slug_starts_with_dash():
    with pytest.raises(HTTPException):
        validate_slug("-foo")


def test_validate_slug_ends_with_dash():
    with pytest.raises(HTTPException):
        validate_slug("foo-")


def test_validate_slug_valid():
    validate_slug("my-report")
    validate_slug("report2024")
    validate_slug("a")


def test_safe_path_normal():
    assert _safe_path("foo/bar.html") == "foo/bar.html"
    assert _safe_path("index.html") == "index.html"


def test_safe_path_traversal():
    with pytest.raises(HTTPException):
        _safe_path("../etc/passwd")


def test_safe_path_absolute():
    with pytest.raises(HTTPException):
        _safe_path("/etc/passwd")


def test_safe_path_empty():
    assert _safe_path("") is None


def test_safe_path_root_dir_entry():
    # "/" is a root directory entry in archives; skip it rather than raising
    assert _safe_path("/") is None


def test_safe_path_normalizes_dotslash():
    assert _safe_path("./foo.html") == "foo.html"


def test_strip_common_prefix_strips():
    paths = ["dist/index.html", "dist/styles.css", "dist/app.js"]
    assert _strip_common_prefix(paths) == ["index.html", "styles.css", "app.js"]


def test_strip_common_prefix_no_common():
    paths = ["index.html", "styles.css"]
    assert _strip_common_prefix(paths) == ["index.html", "styles.css"]


def test_strip_common_prefix_mixed():
    paths = ["a/index.html", "b/styles.css"]
    assert _strip_common_prefix(paths) == ["a/index.html", "b/styles.css"]


def test_strip_common_prefix_none_passthrough():
    paths = [None, "dist/index.html", "dist/styles.css"]
    result = _strip_common_prefix(paths)
    assert result == [None, "index.html", "styles.css"]
