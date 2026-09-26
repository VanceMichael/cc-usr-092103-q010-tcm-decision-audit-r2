"""不可反向修改的审计事件日志。

驳回、修订、不良结果上报、授权撤回等事实一旦写入即不可更改：
事件对象冻结、日志只提供追加接口，整条链以哈希相连，
任何事后改动都会在 verify() 中暴露。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConflictError, ValidationError
from .util import canonical_json, parse_ts, sha256_hex

EVENT_TYPES = frozenset({
    "INFERENCE_COMPLETED",
    "INFERENCE_DENIED",
    "DISPOSITION_CONFIRMED",
    "DISPOSITION_REJECTED",
    "DISPOSITION_REVISED",
    "ADVERSE_OUTCOME_REPORTED",
    "CONSENT_GRANTED",
    "CONSENT_WITHDRAWN",
    "MODEL_VERSION_REGISTERED",
    "MODEL_VERSION_DEACTIVATED",
    "GRAY_RULE_SET",
    "CONSULTATION_REQUESTED",
    "CONSULTATION_RESOLVED",
})

GENESIS = "0" * 64


@dataclass(frozen=True)
class Event:
    event_id: str
    type: str
    occurred_at: str
    recorded_at: str
    actor: str
    payload: dict
    prev_hash: str
    hash: str


def _compute_hash(event_id, type_, occurred_at, recorded_at, actor, payload, prev_hash) -> str:
    body = canonical_json({
        "event_id": event_id,
        "type": type_,
        "occurred_at": occurred_at,
        "recorded_at": recorded_at,
        "actor": actor,
        "payload": payload,
        "prev_hash": prev_hash,
    })
    return sha256_hex(body)


def sorted_events_up_to(events, at: str) -> list:
    """按 (发生时间, 记录时间, 事件ID) 确定次序取 at 之前（含）的事件。

    离线补传的事件 occurred_at 早于写入时间，仍按发生时间参与折叠，
    因此"当时"的快照与补传顺序无关。
    """
    limit = parse_ts(at)
    chosen = [e for e in events if parse_ts(e.occurred_at) <= limit]
    chosen.sort(key=lambda e: (parse_ts(e.occurred_at), parse_ts(e.recorded_at), e.event_id))
    return chosen


class EventLog:
    """只追加日志；同一事件重复提交（如离线补传重试）幂等返回。"""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._by_id: dict[str, Event] = {}

    def append(self, *, event_id, type, occurred_at, recorded_at, actor, payload) -> Event:
        if type not in EVENT_TYPES:
            raise ValidationError(f"未知事件类型: {type}")
        parse_ts(occurred_at)
        parse_ts(recorded_at)
        existing = self._by_id.get(event_id)
        if existing is not None:
            same = (
                existing.type == type
                and existing.occurred_at == occurred_at
                and existing.actor == actor
                and existing.payload == payload
            )
            if same:
                return existing
            raise ConflictError(f"事件 {event_id} 已存在且内容不同")
        prev = self._events[-1].hash if self._events else GENESIS
        digest = _compute_hash(event_id, type, occurred_at, recorded_at, actor, payload, prev)
        event = Event(event_id, type, occurred_at, recorded_at, actor, dict(payload), prev, digest)
        self._events.append(event)
        self._by_id[event_id] = event
        return event

    @property
    def events(self) -> tuple:
        return tuple(self._events)

    def of_type(self, type_: str) -> list:
        return [e for e in self._events if e.type == type_]

    def up_to(self, at: str) -> list:
        return sorted_events_up_to(self._events, at)

    def verify(self) -> bool:
        prev = GENESIS
        for e in self._events:
            if e.prev_hash != prev:
                return False
            if _compute_hash(e.event_id, e.type, e.occurred_at, e.recorded_at,
                             e.actor, e.payload, e.prev_hash) != e.hash:
                return False
            prev = e.hash
        return True
