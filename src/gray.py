"""灰度切换：同一请求在任何时刻重放都解析到同一版本。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import NotFoundError, ValidationError
from .events import sorted_events_up_to
from .util import canonical_json, parse_ts, sha256_hex


@dataclass(frozen=True)
class GrayRule:
    model_version: str
    percent: int
    effective_from: str


def bucket(institution_id: str, request_id: str) -> int:
    """0..99 的稳定分桶，只取决于机构与请求标识。"""
    return int(sha256_hex(f"{institution_id}|{request_id}"), 16) % 100


def rules_at(events, institution_id: str, at: str):
    """最新一条 GRAY_RULE_SET 整体替换该机构规则集。"""
    chosen = None
    for e in sorted_events_up_to(events, at):
        if e.type == "GRAY_RULE_SET" and e.payload.get("institution_id") == institution_id:
            chosen = e
    if chosen is None:
        return [], None
    rules = [GrayRule(model_version=r["model_version"], percent=int(r["percent"]),
                      effective_from=r["effective_from"])
             for r in chosen.payload["rules"]]
    return rules, chosen.event_id


def resolve_version(*, institution_id, request_id, candidates, rules, at):
    """返回 (版本, 快照)；候选按生效时间升序传入，默认取最新。

    命中灰度规则但版本未在机构登记时跳过该规则；解析不到版本
    属于确定性的拒绝，而不是静默回退。
    """
    b = bucket(institution_id, request_id)
    for rule in rules:
        if (parse_ts(rule.effective_from) <= parse_ts(at)
                and rule.model_version in candidates
                and b < rule.percent):
            return rule.model_version, {
                "bucket": b,
                "matched_rule": {"model_version": rule.model_version,
                                 "percent": rule.percent,
                                 "effective_from": rule.effective_from},
                "candidates": list(candidates),
            }
    if not candidates:
        raise NotFoundError(f"机构 {institution_id} 无可用模型版本")
    return candidates[-1], {"bucket": b, "matched_rule": None, "candidates": list(candidates)}


class GrayService:
    def __init__(self, log, registry, roles=None):
        self._log = log
        self._registry = registry
        self._roles = roles

    def set_rules(self, *, institution_id, rules, at, by, by_role="org_admin", event_id=None):
        if self._roles:
            self._roles.require(by_role, "gray:manage")
        self._registry.institution(institution_id)
        normalized = []
        for r in rules:
            percent = int(r["percent"])
            if not 0 <= percent <= 100:
                raise ValidationError(f"灰度比例越界: {percent}")
            normalized.append({"model_version": r["model_version"], "percent": percent,
                               "effective_from": r["effective_from"]})
        payload = {"institution_id": institution_id, "rules": normalized}
        return self._log.append(
            event_id=event_id or "gray-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="GRAY_RULE_SET", occurred_at=at, recorded_at=at, actor=by, payload=payload)
