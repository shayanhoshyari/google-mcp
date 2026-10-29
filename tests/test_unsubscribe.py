"""Offline tests for List-Unsubscribe parsing, list_senders grouping, and the one-click guard."""

from __future__ import annotations

from typing import Any

import pytest

from google_mcp import gmail

ONE_CLICK = {"list-unsubscribe-post": "List-Unsubscribe=One-Click"}


def _payload(headers: dict[str, str]) -> dict[str, Any]:
    return {"payload": {"headers": [{"name": name, "value": value} for name, value in headers.items()]}}


def test_one_click_and_mailto() -> None:
    result = gmail._unsubscribe(
        {"list-unsubscribe": "<mailto:u@example.com?subject=unsub>, <https://example.com/u?id=1>", **ONE_CLICK}
    )
    assert result == {
        "one_click_url": "https://example.com/u?id=1",
        "mailto": {"to": "u@example.com", "subject": "unsub"},
    }


def test_mailto_query_values_are_decoded() -> None:
    result = gmail._unsubscribe(
        {"list-unsubscribe": "<mailto:list%2Bunsub@example.com?subject=Remove%20me&body=please%20remove>"}
    )
    assert result == {"mailto": {"to": "list+unsub@example.com", "subject": "Remove me", "body": "please remove"}}


def test_https_without_post_header_is_a_plain_link() -> None:
    assert gmail._unsubscribe({"list-unsubscribe": "<https://example.com/u>"}) == {"urls": ["https://example.com/u"]}


def test_http_is_never_one_click() -> None:
    result = gmail._unsubscribe({"list-unsubscribe": "<http://example.com/u>", **ONE_CLICK})
    assert result == {"urls": ["http://example.com/u"]}


def test_post_value_is_case_insensitive() -> None:
    result = gmail._unsubscribe(
        {"list-unsubscribe": "<https://example.com/u>", "list-unsubscribe-post": "list-unsubscribe=one-click"}
    )
    assert result == {"one_click_url": "https://example.com/u"}


@pytest.mark.parametrize(
    "value", ["", "garbage", "<>", "<javascript:alert(1)>", "<https:///nohost>", "<mailto:nobody>"]
)
def test_malformed_or_empty_header(value: str) -> None:
    assert gmail._unsubscribe({"list-unsubscribe": value, **ONE_CLICK}) is None


def test_missing_header() -> None:
    assert gmail._unsubscribe({}) is None


def test_several_uris_with_whitespace_and_folding() -> None:
    result = gmail._unsubscribe(
        {
            "list-unsubscribe": "<https://a.example.com/one\r\n click>,\r\n\t<https://b.example.com/two>, "
            "<mailto:\r\n z@example.com>",
            "list-unsubscribe-post": "List-Unsubscribe=\r\n One-Click",
        }
    )
    assert result == {
        "one_click_url": "https://a.example.com/oneclick",
        "urls": ["https://b.example.com/two"],
        "mailto": {"to": "z@example.com"},
    }


def test_metadata_carries_unsubscribe() -> None:
    message = {"id": "m1", **_payload({"List-Unsubscribe": "<https://example.com/u>", **_title_case(ONE_CLICK)})}
    assert gmail._normalize_metadata(message)["unsubscribe"] == {"one_click_url": "https://example.com/u"}


def _title_case(headers: dict[str, str]) -> dict[str, str]:
    return {"-".join(part.capitalize() for part in name.split("-")): value for name, value in headers.items()}


@pytest.fixture
def no_post(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    posted: list[Any] = []
    monkeypatch.setattr(gmail.requests, "post", lambda *args, **kwargs: posted.append(args))
    return posted


@pytest.mark.parametrize(
    "headers",
    [
        {"From": "a@example.com", "List-Unsubscribe": "<https://example.com/u>"},
        {"From": "a@example.com", "List-Unsubscribe": "<http://example.com/u>", **_title_case(ONE_CLICK)},
        {"From": "a@example.com", "List-Unsubscribe": "<mailto:u@example.com>", **_title_case(ONE_CLICK)},
        {"From": "a@example.com"},
        {"From": "a@example.com", "List-Unsubscribe": "<https://localhost/u>", **_title_case(ONE_CLICK)},
        {"From": "a@example.com", "List-Unsubscribe": "<https://10.0.0.1/u>", **_title_case(ONE_CLICK)},
        {"From": "a@example.com", "List-Unsubscribe": "<https://127.0.0.1:8080/u>", **_title_case(ONE_CLICK)},
    ],
)
def test_one_click_refuses_before_any_post(
    monkeypatch: pytest.MonkeyPatch, no_post: list[Any], headers: dict[str, str]
) -> None:
    monkeypatch.setattr(gmail, "request", lambda *args, **kwargs: _payload(headers))
    with pytest.raises(ValueError):
        gmail.unsubscribe_one_click("m1")
    assert no_post == []


def test_one_click_posts_rfc_8058_body(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = {
        "From": "News <news@example.com>",
        "List-Unsubscribe": "<https://example.com/u?t=1>",
        **_title_case(ONE_CLICK),
    }
    monkeypatch.setattr(gmail, "request", lambda *args, **kwargs: _payload(headers))
    monkeypatch.setattr(gmail, "_check_public_host", lambda host: None)  # no DNS in tests
    calls: list[tuple[str, dict[str, Any]]] = []

    class Response:
        status_code = 202

    monkeypatch.setattr(gmail.requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    result = gmail.unsubscribe_one_click("m1")
    assert result == {"sender": "News <news@example.com>", "host": "example.com", "status": 202, "ok": True}
    assert calls == [
        (
            "https://example.com/u?t=1",
            {"data": {"List-Unsubscribe": "One-Click"}, "timeout": 15, "allow_redirects": False},
        )
    ]


def test_list_senders_groups_by_address(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = {
        "a": ("A <a@example.com>", "1000", ["CATEGORY_PROMOTIONS"], "s1", "<https://example.com/u>"),
        "b": ("a@EXAMPLE.com", "3000", ["CATEGORY_UPDATES"], "s2", None),
        "c": ("B <b@example.org>", "2000", [], "s3", None),
    }

    def fake_request(method: str, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if url.endswith("/messages"):
            return {"messages": [{"id": key} for key in messages], "nextPageToken": "more"}
        key = url.rsplit("/", 1)[1]
        sender, date, labels, subject, unsubscribe = messages[key]
        headers = {"From": sender, "Subject": subject}
        if unsubscribe:
            headers["List-Unsubscribe"] = unsubscribe
        return {"id": key, "internalDate": date, "labelIds": labels, **_payload(headers)}

    monkeypatch.setattr(gmail, "request", fake_request)
    monkeypatch.setattr(gmail.auth, "access_token", lambda **kwargs: "token")
    result = gmail.list_senders(max_messages=3)
    assert result["scanned"] == 3 and result["more_messages"] is True
    assert sum(row["count"] for row in result["senders"]) == 3
    top = result["senders"][0]
    assert top == {
        "address": "a@example.com",
        "name": "A",
        "count": 2,
        "latest_date": "1970-01-01T00:00:03Z",
        "latest_message_id": "b",
        "subjects": ["s2", "s1"],
        "labels": ["CATEGORY_PROMOTIONS", "CATEGORY_UPDATES"],
        "unsubscribe": {"urls": ["https://example.com/u"]},
    }


def test_list_senders_retries_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        attempts.append(url)
        if len(attempts) == 1:
            raise gmail.GoogleAPIError("slow down", status=429)
        return {"id": "m1"}

    monkeypatch.setattr(gmail, "request", fake_request)
    monkeypatch.setattr(gmail.time, "sleep", lambda seconds: None)
    assert gmail._metadata("m1") == {"id": "m1"}
    assert len(attempts) == 2
