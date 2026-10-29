# Google MCP — Unsubscribe Plan

## Goal

Let an agent find bulk senders, report how each one can be unsubscribed, and unsubscribe from the ones the user approves. Only future mail matters: no archive, delete, labels, or filters. No new OAuth scopes.

## How unsubscribing works

Bulk mail carries the `List-Unsubscribe` header (RFC 2369): one or more `<https://...>` or `<mailto:...>` URIs. When the message also carries `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058), an HTTPS POST of `List-Unsubscribe=One-Click` to the HTTPS URI unsubscribes with no page to visit. This is what Gmail's own Unsubscribe button uses. The Gmail API has no unsubscribe endpoint.

| Method | How the agent handles it |
|---|---|
| One-click (`https` URI plus `List-Unsubscribe-Post`) | `unsubscribe_one_click` on a message from that sender. |
| `mailto:` | `create_draft` to that address with its `subject`/`body` query values; the user sends it. |
| Plain `https` URI without one-click | Listed for the user to open in a browser. |
| No header (link only in the body) | Listed for the user, with the message ID to find it. |

## Changes

### Parsed unsubscribe field

`list_emails`, `read_email`, and `read_thread` request `List-Unsubscribe` and `List-Unsubscribe-Post` and return, when present:

```json
"unsubscribe": {
  "one_click_url": "https://...",
  "urls": ["https://..."],
  "mailto": {"to": "unsub@example.com", "subject": "...", "body": "..."}
}
```

`one_click_url` is set only for an `https` URI on a message that has the one-click `List-Unsubscribe-Post` value. `urls` holds the other `http(s)` URIs.

### `list_senders`

| Parameters | Behavior |
|---|---|
| `query?` (default `newer_than:90d -in:sent -in:chats`), `max_messages?` (1–2000, default 500) | Read metadata for up to `max_messages` matching messages and group them by sender address. Read-only. |

Each row: `address`, `name`, `count`, `latest_date`, `latest_message_id`, up to three recent `subjects`, `labels` (the union of Gmail category labels such as `CATEGORY_PROMOTIONS`), and `unsubscribe` from the most recent message that has one. Rows sort by `count`, descending. The result also reports `scanned` and whether the query had more messages than `max_messages`.

Metadata requests run on a small thread pool (about 8 workers) to stay within Gmail's per-user quota (`messages.get` costs 5 of 250 units per second), so 500 messages take about 10 seconds.

### `unsubscribe_one_click`

| Parameters | Behavior |
|---|---|
| `message_id` | Re-read the message headers; require the one-click `List-Unsubscribe-Post` value and an `https` URI; POST `List-Unsubscribe=One-Click` (`application/x-www-form-urlencoded`) with no cookies or credentials, a 15-second timeout, and no redirects followed. Return the sender, URL host, and HTTP status. |

The URL always comes from the message header, never from a tool argument, so the tool cannot be used to POST to arbitrary URLs. Added during implementation: the host must resolve only to public addresses (no loopback, private, or link-local), since the sender writes the header. This is the server's only request outside Google. Its description says it acts outside Gmail and must only run on senders the user approved. A 2xx status counts as success; anything else is reported, not retried.

## Docs and rules

- `DESIGN.md`: the `unsubscribe` field, both tools, and one-click as the single non-Google request.
- `AGENTS.md`: Gmail stays read-only except drafts and one-click unsubscribe from a message's own header.
- `README.md` tool list, `CHANGELOG.md` and `pyproject.toml` (0.5.0). No new scopes, so no reauthorization beyond the usual weekly one.
- Added during implementation: `tests/test_unsubscribe.py` (offline pytest for the parser, `list_senders` grouping and rate-limit retry, and the one-click guard), `pytest` in the dev extra, `make test`, and `AGENTS.md` asks for it before handoff.

## Test session

Steps marked **User** need the user; the agent stops there, asks, and continues once the user confirms.

### Offline (agent)

1. Parser on sample header values: one-click `https` plus `mailto`; `mailto` with `subject`/`body` query values; plain `https` without `List-Unsubscribe-Post`; `http` only (not one-click); malformed or empty header; several URIs with whitespace and line folding.
2. `unsubscribe_one_click` refuses a message without the one-click header and a non-`https` URI, before any network call.
3. `make lint` passes.

### Install (user)

4. **User**: run `make install` in `agents/mcp/google_mcp`, then restart the MCP client.
5. Agent runs `status` (through `~/.claude/google_mcp/venv/bin/python -m google_mcp status`). No new scopes, so `missing_scopes` stays empty. **User**, only if the token has expired (Testing mode, every seven days): run `google-mcp auth`, then restart the MCP client.

### Live, read-only (agent)

6. `list_emails` with `category:promotions` returns `unsubscribe` on bulk messages and none on personal mail.
7. `read_email` on one of them shows the same `unsubscribe` as `list_emails`.
8. `list_senders` with the default query and `max_messages` 500: finishes in under a minute, no quota errors, counts add up to `scanned`, rows sorted by count.

### Live, one-click and draft

9. **User**: pick one sender to unsubscribe from as the test, from the `list_senders` output, with a one-click method.
10. Agent calls `unsubscribe_one_click` on that sender's latest message; reports the HTTP status.
11. **User**: pick one sender with only a `mailto:` method (skip if none). Agent creates the draft; **User** checks it in Gmail Drafts and sends it or deletes it.
12. **User**, a few days later: confirm no new mail from the step 9 sender. This does not block release.

## Not in this version

Archive, delete, labels, filters, block sender, following body links, opening browser pages, and sending the `mailto:` unsubscribe (drafts only).

## Results (2026-10-28)

All steps done except step 12, which is a later check.

1–3. Offline: kept as `tests/test_unsubscribe.py` (24 tests, `make test`); `make lint` passes.
4–5. Reinstalled; `status` shows no `missing_scopes`.
6–7. Bulk messages carry `unsubscribe`; sent mail and transactional mail (login codes, security alerts) have none. `read_email` matches `list_emails`.
8. `list_senders` with the defaults: 500 messages in about 24 seconds (not 10), no quota errors, counts add up to `scanned`, sorted by count. 101 of 178 senders offered one-click, 3 only `mailto:`, 3 only a plain link, 71 no header. The tool description says about 25 seconds.
9–10. One-click unsubscribe from a Klaviyo-hosted retail list returned HTTP 200.
11. The only `mailto:`-only senders were members posting to a Mailman list; a draft to its `-leave` address (no subject or body in the header) was created and sent by the user.
12. Pending: confirm no new mail from the step 9 and 11 lists after a few days.

## Docs cleanup (requested during review)

The user asked, while reviewing this plan, to remove overlap between the docs. Done as a separate commit after the unsubscribe commit:

- `AGENTS.md`: only what an agent needs before editing: `DESIGN.md` first, the `make` commands, credentials, one line on not widening the write surface (linking to the scopes and security sections), and publish safety. The repeated scope and tool rules are gone.
- `INSTALL.md`: everything about setup, moved from `README.md`: Google Cloud setup and scopes, install, `configure`/`auth`/`status`, updates (now generic, pointing at `CHANGELOG.md` instead of a per-version list that had gone stale), local files and client registration, removal.
- `README.md`: a landing page: what it is, what it can change, the tool list, and links. The per-tool behavior sections (drafts, unsubscribing, Drive) duplicated `DESIGN.md` §5 and are removed, as is the stale manual test session (test sessions live in `plans/`).
- `DESIGN.md` §2 and §8: links and the docs rule updated to match.


## Review fixes (2026-10-28)

Released as 0.5.1 (`pyproject.toml`, `uv.lock`, `CHANGELOG.md`):

- `list_senders` rows add `unsubscribe_message_id`, the message the `unsubscribe` value came from. `latest_message_id` is the newest message, which may have no header, so passing it to `unsubscribe_one_click` could fail. The `unsubscribe_one_click` `message_id` description and the `list_senders` description now name `unsubscribe_message_id`; `DESIGN.md` lists the field.
- `bounded_int` takes a `name` (default `max_results`) for its error messages; `list_senders` and `read_thread` pass `max_messages`. The other callers take `max_results`.

`tests/test_unsubscribe.py`: the grouping test now checks that `unsubscribe_message_id` points at the older message with the header while `latest_message_id` points at the newer one without it, and is absent for a sender with no header. New tests cover the `max_messages` name in `list_senders` and `read_thread` errors and the `max_results` default. `make test`: 29 passed; `make lint` passes.
