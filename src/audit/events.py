"""只增哈希链事件日志。

任何对临床决定产生影响的动作都以事件追加：请求受理/驳回、
模型输出、医生确认/修订/驳回、不良结果、授权撤回、版本停用、
科研导出。事件一经写入不可修改或删除；校验时逐条重算哈希链。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .serde import canonical, digest

GENESIS = "sha256:" + "0" * 64  # 创世前驱


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    seq: int
    event_id: str
    event_type: str
    occurred_at: str
    recorded_at: str
    actor: dict
    org_id: str | None
    payload: dict
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "actor": self.actor,
            "org_id": self.org_id,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


class TamperError(RuntimeError):
    pass


class EventLog:
    """内存哈希链；可导入/导出 JSONL，重载时强制校验。"""

    def __init__(self):
        self._events: list[Event] = []

    # ---- 写入 -------------------------------------------------
    def append(self, event_type: str, payload: dict, *, occurred_at: str,
               actor: dict, org_id: str | None = None,
               event_id: str | None = None) -> Event:
        seq = len(self._events) + 1
        eid = event_id or f"EVT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{seq:04d}"
        prev_hash = self._events[-1].hash if self._events else GENESIS
        body = {
            "seq": seq,
            "event_id": eid,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "recorded_at": _now(),
            "actor": actor,
            "org_id": org_id,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        event = Event(hash=digest(body), **body)
        self._events.append(event)
        return event

    # ---- 读取 -------------------------------------------------
    def all(self) -> list[Event]:
        return list(self._events)

    def by_id(self, event_id: str) -> Event | None:
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    def of_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def head_hash(self) -> str:
        return self._events[-1].hash if self._events else GENESIS

    def __len__(self) -> int:
        return len(self._events)

    # ---- 校验 -------------------------------------------------
    def verify(self) -> None:
        """重算整条链；任何插入、删除、改写都会在此暴露。"""
        prev = GENESIS
        for i, event in enumerate(self._events, start=1):
            if event.seq != i:
                raise TamperError(f"序号断裂：期望 {i}，实际 {event.seq}")
            if event.prev_hash != prev:
                raise TamperError(f"事件 {event.event_id} 前驱哈希不匹配（链被插入/删除）")
            body = {k: v for k, v in event.to_dict().items() if k != "hash"}
            if digest(body) != event.hash:
                raise TamperError(f"事件 {event.event_id} 内容哈希不匹配（记录被改写）")
            prev = event.hash

    # ---- 持久化 -----------------------------------------------
    def export_jsonl(self) -> str:
        return "".join(json.dumps(e.to_dict(), ensure_ascii=False) + "\n"
                       for e in self._events)

    @classmethod
    def import_jsonl(cls, text: str) -> "EventLog":
        log = cls()
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            event = Event(**raw)
            if event.seq != len(log._events) + 1:
                raise TamperError(f"第 {line_no} 行序号不连续")
            log._events.append(event)
        log.verify()
        return log
