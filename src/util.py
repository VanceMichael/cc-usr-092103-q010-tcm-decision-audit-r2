"""确定性工具：规范 JSON、哈希与时间比较。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def canonical_json(obj) -> str:
    """排序键、无空白的 JSON，用于哈希与指纹。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_ts(ts: str) -> datetime:
    """解析 ISO-8601 时间；缺省时区按 UTC 处理。"""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
