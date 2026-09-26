"""规范化序列化、摘要、化名与稳定分桶。

所有入库内容都先规范化（键排序、紧凑分隔、不转义非 ASCII），
保证“同一输入永远得到同一摘要”，这是重放与幂等的基础。
"""

import hashlib
import hmac
import json
from datetime import datetime


def canonical(obj) -> bytes:
    """返回确定性字节序列：键排序、无空白、保留中文。"""
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(obj) -> str:
    """对任意可 JSON 化对象计算 sha256 摘要。"""
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def pseudonym(identifier: str, salt: str, prefix: str = "PSEUD") -> str:
    """用独立盐值生成不可逆化名；盐值不进入日志。"""
    mac = hmac.new(salt.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256)
    return f"{prefix}-" + mac.hexdigest()[:12].upper()


def stable_bucket(key: str, salt: str) -> int:
    """把稳定键映射到 0..99 的桶；同键永远同桶（灰度确定性）。"""
    mac = hmac.new(salt.encode("utf-8"), key.encode("utf-8"), hashlib.sha256)
    return int.from_bytes(mac.digest()[:4], "big") % 100


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def iso(dt: datetime) -> str:
    return dt.isoformat()
