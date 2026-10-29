"""Offline tests for deriving a plain-text body from HTML-only messages."""

from __future__ import annotations

import base64
from typing import Any

from google_mcp import gmail


def _html_payload(html: str) -> dict[str, Any]:
    data = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    return {"mimeType": "text/html", "body": {"data": data}}


def _text(html: str) -> str:
    return gmail._body(_html_payload(html))[0]


def test_links_keep_their_url() -> None:
    assert _text('<p>Read <a href="https://example.com/post?a=1&amp;copy=2">the post</a>.</p>') == (
        "Read [the post](https://example.com/post?a=1&copy=2)."
    )


def test_bare_url_and_mailto_links_are_not_repeated() -> None:
    html = '<a href="https://example.com">https://example.com</a> <a href="mailto:a@example.com">a@example.com</a>'
    assert _text(html) == "https://example.com mailto:a@example.com"


def test_image_link_uses_alt_or_url() -> None:
    html = '<a href="https://example.com/a"><img alt="Shop"></a><a href="https://example.com/b"><img src="x.png"></a>'
    assert _text(html) == "[Shop](https://example.com/a)https://example.com/b"


def test_non_web_links_stay_text() -> None:
    assert _text('<a href="#top">Top</a> <a href="javascript:void(0)">Menu</a> <a>Plain</a>') == "Top Menu Plain"


def test_unclosed_link_is_flushed() -> None:
    assert _text('<div>Go <a href="https://example.com/x">here') == "Go [here](https://example.com/x)"
