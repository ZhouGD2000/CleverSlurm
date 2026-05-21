import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from cslurm.config import (
    feishu_message_format,
    feishu_secret,
    feishu_webhook_url,
    notification_batch_window_minutes,
    notification_enabled,
    notification_immediate_group_threshold,
)
from cslurm.db import connect, init_db


DEFAULT_URLOPEN = urllib.request.urlopen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_feishu_sign(secret: str, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _card_template(severity: str | None) -> str:
    if severity == "high":
        return "red"
    if severity == "medium":
        return "orange"
    if severity == "low":
        return "grey"
    return "blue"


def build_text_payload(text: str, *, secret: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = timestamp
        payload["sign"] = build_feishu_sign(secret, timestamp)
    return payload


def build_card_payload(title: str, body: str, *, severity: str | None = None, secret: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": _card_template(severity),
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": body},
                }
            ],
        },
    }
    if secret:
        timestamp = int(time.time())
        payload["timestamp"] = timestamp
        payload["sign"] = build_feishu_sign(secret, timestamp)
    return payload


def send_payload(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    urlopen=None,
    timeout: int = 10,
    attempts: int = 3,
    backoff_seconds: float = 0.2,
) -> None:
    urlopen = urlopen or DEFAULT_URLOPEN
    data = json.dumps(payload, ensure_ascii=False).encode()
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode() or "{}")
            code = body.get("code")
            status_code = body.get("StatusCode")
            if code not in (None, 0) or status_code not in (None, 0):
                message = body.get("msg") or body.get("StatusMessage") or body
                raise RuntimeError(f"Feishu webhook rejected message: {message}")
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(backoff_seconds * (2**attempt))
    raise RuntimeError(f"Feishu webhook send failed: {last_error}") from last_error


def _payload_for_notification(row: dict[str, Any], secret: str | None) -> dict[str, Any]:
    if feishu_message_format() == "text":
        return build_text_payload(f"{row['title']}\n\n{row['body']}", secret=secret)
    return build_card_payload(row["title"], row["body"], severity=row.get("severity"), secret=secret)


def _summarize_group(mode: str, group_id: str, rows: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    categories = Counter(row.get("category") or "UNKNOWN" for row in rows)
    severities = Counter(row.get("severity") or "unknown" for row in rows)
    statuses = Counter()
    representatives = []
    for row in rows:
        payload = json.loads(row.get("payload_json") or "{}")
        analysis = payload.get("analysis") or {}
        statuses[analysis.get("semantic_status") or analysis.get("deterministic_status") or "unknown"] += 1
        if len(representatives) < 5:
            representatives.append(
                {
                    "job_id": row.get("job_id"),
                    "category": row.get("category"),
                    "severity": row.get("severity"),
                    "title": row.get("title"),
                }
            )

    summary = {
        "total": len(rows),
        "group_id": group_id,
        "mode": mode,
        "categories": dict(categories),
        "severities": dict(severities),
        "semantic_statuses": dict(statuses),
        "representative_jobs": representatives,
    }
    labels = {"digest": "DIGEST", "immediate": "IMMEDIATE"}
    label = labels.get(mode, "BATCH")
    title = f"[CleverSlurm][{label}] {group_id}: {len(rows)} job(s)"
    lines = [
        f"Group: {group_id}",
        f"Mode: {mode}",
        f"Total: {len(rows)}",
        "",
        "Categories:",
    ]
    for category, count in categories.most_common():
        lines.append(f"- {category}: {count}")
    if statuses:
        lines.append("")
        lines.append("Semantic status:")
        for status, count in statuses.most_common():
            lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append("Representative jobs:")
    for item in representatives:
        lines.append(f"- {item['job_id']}: {item['category']} ({item['severity']})")
    return title, "\n".join(lines), summary


def _group_key(row: dict[str, Any]) -> str:
    return row.get("group_id") or row.get("category") or "ungrouped"


def _is_due(row: dict[str, Any], *, force: bool, window_minutes: int) -> bool:
    if force:
        return True
    created_at = _parse_time(row.get("created_at"))
    if created_at is None:
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at <= datetime.now(timezone.utc) - timedelta(minutes=window_minutes)


def _dispatch_grouped(
    conn,
    *,
    mode: str,
    limit: int,
    force: bool,
    urlopen,
    webhook_url: str,
    secret: str | None,
) -> int:
    rows = conn.execute(
        """
        select *
        from notifications
        where status = 'pending' and channel = 'feishu' and mode = ?
        order by id
        limit ?
        """,
        (mode, limit),
    ).fetchall()
    pending = [
        {key: row[key] for key in row.keys()}
        for row in rows
        if _is_due({key: row[key] for key in row.keys()}, force=force, window_minutes=notification_batch_window_minutes())
    ]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pending:
        groups[_group_key(row)].append(row)

    sent = 0
    for group_id, group_rows in groups.items():
        title, body, summary = _summarize_group(mode, group_id, group_rows)
        window_start = min(row.get("created_at") or "" for row in group_rows)
        window_end = max(row.get("created_at") or "" for row in group_rows)
        cursor = conn.execute(
            """
            insert into notification_batches (
              group_id, mode, channel, created_at, window_start, window_end, status, summary_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                mode,
                "feishu",
                _now(),
                window_start,
                window_end,
                "pending",
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
            ),
        )
        batch_id = int(cursor.lastrowid)
        try:
            payload = build_card_payload(title, body, severity="normal" if mode == "batch" else "low", secret=secret)
            send_payload(webhook_url, payload, urlopen=urlopen)
        except Exception as exc:
            conn.execute(
                "update notification_batches set status = 'failed', summary_json = ? where id = ?",
                (
                    json.dumps({**summary, "last_error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True),
                    batch_id,
                ),
            )
            continue
        notification_ids = [row["id"] for row in group_rows]
        placeholders = ",".join("?" for _ in notification_ids)
        conn.execute(
            f"update notifications set status = 'batched', sent_at = ?, last_error = null where id in ({placeholders})",
            (_now(), *notification_ids),
        )
        conn.execute("update notification_batches set status = 'sent', sent_at = ? where id = ?", (_now(), batch_id))
        sent += 1
    return sent


def _dispatch_immediate(conn, *, limit: int, urlopen, webhook_url: str, secret: str | None) -> int:
    sent = 0
    rows = conn.execute(
        """
        select *
        from notifications
        where status = 'pending' and channel = 'feishu' and mode = 'immediate'
        order by id
        limit ?
        """,
        (limit,),
    ).fetchall()
    if len(rows) >= notification_immediate_group_threshold():
        return _dispatch_grouped(
            conn,
            mode="immediate",
            limit=limit,
            force=True,
            urlopen=urlopen,
            webhook_url=webhook_url,
            secret=secret,
        )
    for row in rows:
        data = {key: row[key] for key in row.keys()}
        try:
            payload = _payload_for_notification(data, secret)
            send_payload(webhook_url, payload, urlopen=urlopen)
        except Exception as exc:
            conn.execute(
                """
                update notifications
                set status = 'failed', retry_count = retry_count + 1, last_error = ?
                where id = ?
                """,
                (f"{type(exc).__name__}: {exc}", data["id"]),
            )
            continue
        conn.execute(
            "update notifications set status = 'sent', sent_at = ?, last_error = null where id = ?",
            (_now(), data["id"]),
        )
        sent += 1
    return sent


def dispatch_pending(*, limit: int = 500, mode: str = "immediate", force: bool = False, urlopen=None) -> int:
    if not notification_enabled():
        return 0
    webhook_url = feishu_webhook_url()
    if not webhook_url:
        return 0
    if mode not in {"immediate", "batch", "digest", "all"}:
        raise ValueError(f"unsupported notification mode: {mode}")

    sent = 0
    with connect() as conn:
        init_db(conn)
        secret = feishu_secret()
        if mode in {"immediate", "all"}:
            sent += _dispatch_immediate(conn, limit=limit, urlopen=urlopen, webhook_url=webhook_url, secret=secret)
        if mode in {"batch", "all"}:
            sent += _dispatch_grouped(
                conn,
                mode="batch",
                limit=limit,
                force=force,
                urlopen=urlopen,
                webhook_url=webhook_url,
                secret=secret,
            )
        if mode in {"digest", "all"}:
            sent += _dispatch_grouped(
                conn,
                mode="digest",
                limit=limit,
                force=force,
                urlopen=urlopen,
                webhook_url=webhook_url,
                secret=secret,
            )
        conn.commit()
    return sent
