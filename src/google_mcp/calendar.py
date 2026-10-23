"""Google Calendar operations."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote

from google_mcp import auth
from google_mcp.google_api import request
from google_mcp.utils import (
    bounded_int,
    compact,
    default_event_window,
    parse_date,
    parse_datetime,
    require_text,
    rfc3339,
)

BASE_URL = "https://www.googleapis.com/calendar/v3"


def _calendar_url(calendar_id: str, suffix: str = "") -> str:
    return f"{BASE_URL}/calendars/{quote(require_text(calendar_id, 'calendar_id'), safe='')}{suffix}"


def _attendee(value: dict[str, Any]) -> dict[str, Any]:
    return compact(
        {
            "email": value.get("email"),
            "display_name": value.get("displayName"),
            "response_status": value.get("responseStatus"),
            "self": value.get("self"),
            "organizer": value.get("organizer"),
            "comment": value.get("comment"),
        }
    )


def _event(value: dict[str, Any]) -> dict[str, Any]:
    raw_start = value.get("start")
    raw_end = value.get("end")
    raw_organizer = value.get("organizer")
    raw_creator = value.get("creator")
    raw_attendees = value.get("attendees")
    start = cast(dict[str, Any], raw_start) if isinstance(raw_start, dict) else {}
    end = cast(dict[str, Any], raw_end) if isinstance(raw_end, dict) else {}
    organizer = cast(dict[str, Any], raw_organizer) if isinstance(raw_organizer, dict) else {}
    creator = cast(dict[str, Any], raw_creator) if isinstance(raw_creator, dict) else {}
    attendees = cast(list[Any], raw_attendees) if isinstance(raw_attendees, list) else []
    return compact(
        {
            "id": value.get("id"),
            "status": value.get("status"),
            "summary": value.get("summary"),
            "description": value.get("description"),
            "location": value.get("location"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "all_day": "date" in start,
            "start_timezone": start.get("timeZone"),
            "end_timezone": end.get("timeZone"),
            "organizer": compact({"email": organizer.get("email"), "display_name": organizer.get("displayName")}),
            "creator": compact({"email": creator.get("email"), "display_name": creator.get("displayName")}),
            "attendees": [_attendee(item) for item in attendees if isinstance(item, dict)],
            "html_link": value.get("htmlLink"),
            "recurrence": value.get("recurrence"),
            "recurring_event_id": value.get("recurringEventId"),
            "updated": value.get("updated"),
        }
    )


def list_calendars(page_token: str | None = None, max_results: int | None = None) -> dict[str, Any]:
    limit = bounded_int(max_results, default=50, maximum=250)
    params: dict[str, Any] = {"maxResults": limit}
    if page_token:
        params["pageToken"] = page_token
    page = request("GET", f"{BASE_URL}/users/me/calendarList", params=params)
    calendars = []
    for item in page.get("items", []) if isinstance(page.get("items"), list) else []:
        if isinstance(item, dict):
            calendars.append(
                compact(
                    {
                        "id": item.get("id"),
                        "summary": item.get("summary"),
                        "description": item.get("description"),
                        "primary": item.get("primary", False),
                        "selected": item.get("selected", False),
                        "access_role": item.get("accessRole"),
                        "timezone": item.get("timeZone"),
                        "background_color": item.get("backgroundColor"),
                    }
                )
            )
    return compact({"calendars": calendars, "next_page_token": page.get("nextPageToken")})


def list_events(
    start: str | None = None,
    end: str | None = None,
    calendar_id: str = "primary",
    search: str | None = None,
    max_results: int | None = None,
    page_token: str | None = None,
) -> dict[str, Any]:
    default_start, default_end = default_event_window()
    time_min = rfc3339(start, "start") if start else default_start
    time_max = rfc3339(end, "end") if end else default_end
    if parse_datetime(time_min, "start") >= parse_datetime(time_max, "end"):
        raise ValueError("end must be after start")
    params: dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": bounded_int(max_results, default=50, maximum=250),
    }
    if search:
        params["q"] = search
    if page_token:
        params["pageToken"] = page_token
    page = request("GET", _calendar_url(calendar_id, "/events"), params=params)
    raw_items = page.get("items", [])
    values = [_event(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    return compact(
        {
            "calendar_id": calendar_id,
            "time_min": time_min,
            "time_max": time_max,
            "timezone": page.get("timeZone"),
            "events": values,
            "next_page_token": page.get("nextPageToken"),
        }
    )


def get_event(event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
    event_id = require_text(event_id, "event_id")
    value = request("GET", _calendar_url(calendar_id, f"/events/{quote(event_id, safe='')}"))
    return _event(value)


def _event_times(start: str, end: str, all_day: bool) -> tuple[dict[str, str], dict[str, str]]:
    if all_day:
        start_value = parse_date(start, "start")
        end_value = parse_date(end, "end")
    else:
        start_value = rfc3339(start, "start")
        end_value = rfc3339(end, "end")
    starts_after_end = (
        start_value >= end_value
        if all_day
        else parse_datetime(start_value, "start") >= parse_datetime(end_value, "end")
    )
    if starts_after_end:
        raise ValueError("end must be after start; an all-day end date is exclusive")
    key = "date" if all_day else "dateTime"
    return {key: start_value}, {key: end_value}


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str = "primary",
    all_day: bool = False,
) -> dict[str, Any]:
    start_value, end_value = _event_times(start, end, all_day)
    body = compact(
        {
            "summary": require_text(summary, "summary"),
            "start": start_value,
            "end": end_value,
            "description": description,
            "location": location,
        }
    )
    value = request("POST", _calendar_url(calendar_id, "/events"), params={"sendUpdates": "none"}, json_body=body)
    return _event(value)


def update_event(
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
) -> dict[str, Any]:
    event_id = require_text(event_id, "event_id")
    if (start is None) != (end is None):
        raise ValueError("start and end must be supplied together")
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = require_text(summary, "summary")
    if start is not None and end is not None:
        use_all_day = bool(all_day) if all_day is not None else len(start) == 10 and len(end) == 10
        body["start"], body["end"] = _event_times(start, end, use_all_day)
    elif all_day is not None:
        raise ValueError("all_day can only be changed when start and end are supplied")
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if not body:
        raise ValueError("Provide at least one field to update")
    value = request(
        "PATCH",
        _calendar_url(calendar_id, f"/events/{quote(event_id, safe='')}"),
        params={"sendUpdates": "all"},
        json_body=body,
    )
    return _event(value)


def delete_event(event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
    event_id = require_text(event_id, "event_id")
    request(
        "DELETE",
        _calendar_url(calendar_id, f"/events/{quote(event_id, safe='')}"),
        params={"sendUpdates": "all"},
    )
    return {"deleted": True, "event_id": event_id, "calendar_id": calendar_id}


def respond_to_event(
    event_id: str,
    response: str,
    calendar_id: str = "primary",
    comment: str | None = None,
) -> dict[str, Any]:
    event_id = require_text(event_id, "event_id")
    response = require_text(response, "response").lower()
    if response not in {"accepted", "declined", "tentative"}:
        raise ValueError("response must be accepted, declined, or tentative")
    raw = request("GET", _calendar_url(calendar_id, f"/events/{quote(event_id, safe='')}"))
    attendees = raw.get("attendees")
    if not isinstance(attendees, list):
        raise ValueError("This event has no attendee list, so it cannot be answered as an invitation")
    email = auth.account_email()
    found = False
    updated_attendees: list[dict[str, Any]] = []
    for raw_attendee in attendees:
        if not isinstance(raw_attendee, dict):
            continue
        attendee = dict(raw_attendee)
        is_self = attendee.get("self") is True or (
            email is not None and str(attendee.get("email", "")).casefold() == email.casefold()
        )
        if is_self:
            attendee["responseStatus"] = response
            if comment is not None:
                attendee["comment"] = comment
            found = True
        updated_attendees.append(attendee)
    if not found:
        raise ValueError("The authorized Google account was not found in this event's attendee list")
    value = request(
        "PATCH",
        _calendar_url(calendar_id, f"/events/{quote(event_id, safe='')}"),
        params={"sendUpdates": "all"},
        json_body={"attendees": updated_attendees},
    )
    return _event(value)
