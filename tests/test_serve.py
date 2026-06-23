from app.serve import inject_base, resolve_paths


def test_inject_base_after_head():
    html = b"<html><head><title>Test</title></head><body></body></html>"
    result = inject_base(html, "mysite")
    assert b'<base href="/s/mysite/">' in result
    assert result.index(b'<base href="/s/mysite/">') > result.index(b"<head>")


def test_inject_base_case_insensitive_head():
    html = b"<HTML><HEAD></HEAD><BODY></BODY></HTML>"
    result = inject_base(html, "mysite")
    assert b'<base href="/s/mysite/">' in result


def test_inject_base_head_with_attrs():
    html = b'<head lang="en"><title>Hi</title></head>'
    result = inject_base(html, "docs")
    assert b'<base href="/s/docs/">' in result


def test_inject_base_no_head_prepends():
    html = b"<p>Hello world</p>"
    result = inject_base(html, "mysite")
    assert result.startswith(b'<base href="/s/mysite/">')


def test_inject_base_only_one_tag():
    html = b"<head><title>T</title></head>"
    result = inject_base(html, "s")
    assert result.count(b"<base") == 1


def test_resolve_paths_root():
    assert resolve_paths("") == ["index.html"]
    assert resolve_paths("/") == ["index.html"]


def test_resolve_paths_file_with_extension():
    assert resolve_paths("styles.css") == ["styles.css"]
    assert resolve_paths("images/logo.png") == ["images/logo.png"]


def test_resolve_paths_no_extension_tries_index():
    candidates = resolve_paths("about")
    assert candidates[0] == "about/index.html"
    assert "about" in candidates


def test_resolve_paths_trailing_slash():
    candidates = resolve_paths("about/")
    assert candidates[0] == "about/index.html"


def test_resolve_paths_nested():
    assert resolve_paths("docs/api/reference.html") == ["docs/api/reference.html"]
