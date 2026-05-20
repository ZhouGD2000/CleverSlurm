import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ai_slurm.config import feishu_message_format, feishu_secret, feishu_webhook_url, notification_enabled
from ai_slurm.db import connect, init_db


DEFAULT_URLOPEN = urllib.request.urlopen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def dispatch_pending(*, limit: int = 50, urlopen=None) -> int:
    if not notification_enabled():
        return 0
    webhook_url = feishu_webhook_url()
    if not webhook_url:
        return 0

    sent = 0
    with connect() as conn:
        init_db(conn)
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
        for row in rows:
            data = {key: row[key] for key in row.keys()}
            try:
                payload = _payload_for_notification(data, feishu_secret())
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
        conn.commit()
    return sent
