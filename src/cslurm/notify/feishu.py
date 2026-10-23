import base64
import fcntl
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
    consistent_config,
    root_dir,
    feishu_message_format,
    feishu_secret,
    feishu_webhook_url,
    notification_batch_window_minutes,
    notification_enabled,
    notification_immediate_group_threshold,
)
from cslurm.db import run_write_transaction


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


def _claim(mode, limit, force):
    """Claim and build delivery units in one short write transaction."""
    def claim(conn):
        conn.execute("begin immediate")
        rows = [dict(r) for r in conn.execute(
            "select * from notifications where status = 'pending' and channel = 'feishu' and mode = ? "
            "and not exists (select 1 from notification_exclusions e where e.job_id = notifications.job_id) "
            "order by id limit ?", (mode, limit))]
        grouped = mode != "immediate" or len(rows) >= notification_immediate_group_threshold()
        groups = defaultdict(list)
        for row in rows:
            if mode != "immediate" and not _is_due(row, force=force, window_minutes=notification_batch_window_minutes()):
                continue
            groups[_group_key(row) if grouped else str(row["id"])].append(row)
        units = []
        for group_id, items in groups.items():
            batch_id = None
            title = body = None
            if grouped:
                title, body, summary = _summarize_group(mode, group_id, items)
                batch_id = conn.execute(
                    "insert into notification_batches(group_id,mode,channel,created_at,window_start,window_end,status,summary_json) "
                    "values (?,?,'feishu',?,?,?,'sending',?)",
                    (group_id, mode, _now(), min(r.get("created_at") or "" for r in items),
                     max(r.get("created_at") or "" for r in items), json.dumps(summary))).lastrowid
            ids = [r["id"] for r in items]
            conn.executemany("update notifications set status = 'sending' where id = ?", [(i,) for i in ids])
            units.append((ids, batch_id, title, body, items[0]))
        return units
    return run_write_transaction(claim)


def _finish(ids, batch_id, status, error=None):
    def record(conn):
        conn.executemany(
            "update notifications set status = ?, sent_at = ?, last_error = ?, retry_count = retry_count + ? "
            "where id = ? and status = 'sending'",
            [(status, _now() if status in {"sent", "batched"} else None, error, int(error is not None), i) for i in ids])
        if batch_id:
            conn.execute("update notification_batches set status = ?, sent_at = ? where id = ?",
                         ("sent" if status == "batched" else status, _now() if status == "batched" else None, batch_id))
    run_write_transaction(record)


@consistent_config
def dispatch_pending(*, limit: int = 500, mode: str = "immediate", force: bool = False, urlopen=None) -> int:
    if not notification_enabled() or not feishu_webhook_url():
        return 0
    if mode not in {"immediate", "batch", "digest", "all"} or not 1 <= limit <= 10000:
        raise ValueError("Invalid notification mode or limit")
    root_dir().mkdir(parents=True, exist_ok=True)
    with (root_dir() / "dispatch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        # The process lock proves no live dispatcher owns these interrupted sends.
        def recover(conn):
            conn.execute("update notifications set status = 'uncertain', last_error = 'Interrupted delivery; verify receipt before retrying' where status = 'sending'")
            conn.execute("update notification_batches set status = 'uncertain' where status = 'sending'")
        run_write_transaction(recover)
        sent = 0
        for kind in (["immediate", "batch", "digest"] if mode == "all" else [mode]):
            for ids, batch_id, title, body, row in _claim(kind, limit, force):
                try:
                    secret = feishu_secret()
                    payload = (build_card_payload(title, body, severity=row.get("severity"), secret=secret)
                               if batch_id else _payload_for_notification(row, secret))
                    # A timeout can mean the receiver accepted the message. Never blindly resend.
                    send_payload(feishu_webhook_url(), payload, urlopen=urlopen, attempts=1)
                except Exception:
                    _finish(ids, batch_id, "uncertain", "Delivery not confirmed; no automatic resend")
                    continue
                _finish(ids, batch_id, "batched" if batch_id else "sent")
                sent += 1
        return sent
