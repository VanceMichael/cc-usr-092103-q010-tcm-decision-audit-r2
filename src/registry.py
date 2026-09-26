"""机构层级与模型版本登记：版本、适用范围及停用条件。

基线登记来自 fixtures/institutions.json；运行期的登记与停用
以事件形式追加（MODEL_VERSION_REGISTERED / MODEL_VERSION_DEACTIVATED），
任意时刻的机构权限都可按发生时间确定性地重建。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError, ValidationError
from .events import sorted_events_up_to
from .util import canonical_json, parse_ts, sha256_hex

TIERS = ("总院", "院区", "区域医疗中心", "医联体")


@dataclass(frozen=True)
class Deactivation:
    after: str | None = None
    adverse_events_at_least: int | None = None


@dataclass(frozen=True)
class Registration:
    model_version: str
    diseases: tuple
    effective_from: str
    deactivate_if: Deactivation = Deactivation()

    def status(self, disease_code: str, at: str, adverse_events: int):
        if parse_ts(at) < parse_ts(self.effective_from):
            return False, "版本尚未生效"
        if disease_code not in self.diseases:
            return False, "病种不在适用范围"
        limit = self.deactivate_if
        if limit.after and parse_ts(at) > parse_ts(limit.after):
            return False, "已过停用日期"
        if (limit.adverse_events_at_least is not None
                and adverse_events >= limit.adverse_events_at_least):
            return False, "不良事件达到停用阈值"
        return True, None


@dataclass(frozen=True)
class Institution:
    institution_id: str
    name: str
    tier: str
    parent: str | None
    registrations: tuple = ()


def parse_registration(raw) -> Registration:
    if not isinstance(raw, dict) or not {"model_version", "diseases", "effective_from"} <= set(raw):
        raise ValidationError(f"登记条目缺少必要字段: {raw!r}")
    cond = raw.get("deactivate_if") or {}
    return Registration(
        model_version=raw["model_version"],
        diseases=tuple(raw["diseases"]),
        effective_from=raw["effective_from"],
        deactivate_if=Deactivation(
            after=cond.get("after"),
            adverse_events_at_least=cond.get("adverse_events_at_least"),
        ),
    )


def count_adverse(events) -> dict:
    """按 (机构, 版本) 统计不良结果上报数。"""
    counts = {}
    for e in events:
        if e.type == "ADVERSE_OUTCOME_REPORTED":
            key = (e.payload["institution_id"], e.payload["model_version"])
            counts[key] = counts.get(key, 0) + 1
    return counts


class InstitutionRegistry:
    def __init__(self, institutions):
        self._by_id = {i.institution_id: i for i in institutions}

    @classmethod
    def load(cls, path) -> "InstitutionRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "institutions" not in data:
            raise ValidationError("机构资料缺少 institutions 字段")
        institutions = []
        for raw in data["institutions"]:
            if not isinstance(raw, dict) or not {"id", "name", "tier"} <= set(raw):
                raise ValidationError(f"机构条目缺少必要字段: {raw!r}")
            if raw["tier"] not in TIERS:
                raise ValidationError(f"未知机构层级: {raw['tier']}")
            institutions.append(Institution(
                institution_id=raw["id"],
                name=raw["name"],
                tier=raw["tier"],
                parent=raw.get("parent"),
                registrations=tuple(parse_registration(r) for r in raw.get("registrations", [])),
            ))
        registry = cls(institutions)
        for inst in institutions:
            if inst.parent and inst.parent not in registry._by_id:
                raise ValidationError(f"机构 {inst.institution_id} 的父机构不存在: {inst.parent}")
        return registry

    def institution(self, institution_id) -> Institution:
        try:
            return self._by_id[institution_id]
        except KeyError:
            raise NotFoundError(f"未知机构: {institution_id}") from None

    def institutions(self) -> tuple:
        return tuple(self._by_id.values())

    def children(self, institution_id) -> tuple:
        return tuple(i for i in self._by_id.values() if i.parent == institution_id)

    def registrations_at(self, institution_id, at, events=()) -> list:
        """基线登记叠加截至 at 的登记/停用事件；同版本以生效时间最新者为准。"""
        inst = self.institution(institution_id)
        regs = list(inst.registrations)
        deactivated = set()
        for e in sorted_events_up_to(events, at):
            payload = e.payload
            if payload.get("institution_id") != institution_id:
                continue
            if e.type == "MODEL_VERSION_REGISTERED":
                regs.append(parse_registration(payload["registration"]))
            elif e.type == "MODEL_VERSION_DEACTIVATED":
                deactivated.add(payload["model_version"])
        regs = [r for r in regs if r.model_version not in deactivated]
        regs.sort(key=lambda r: (parse_ts(r.effective_from), r.model_version))
        by_version = {}
        for r in regs:
            by_version[r.model_version] = r
        return list(by_version.values())

    def candidate_versions(self, institution_id, at, events=()) -> list:
        """at 时刻已生效的登记版本（按生效时间升序）。"""
        return [r.model_version for r in self.registrations_at(institution_id, at, events)
                if parse_ts(r.effective_from) <= parse_ts(at)]

    def permission_snapshot(self, institution_id, model_version, disease_code, at, events=()) -> dict:
        """机构权限 + 获准病种 + 停用条件在 at 时刻的完整快照。"""
        inst = self.institution(institution_id)
        folded = sorted_events_up_to(events, at)
        regs = self.registrations_at(institution_id, at, folded)
        adverse = count_adverse(folded).get((institution_id, model_version), 0)
        reg = next((r for r in regs if r.model_version == model_version), None)
        reasons = []
        if reg is None:
            deactivated = any(
                e.type == "MODEL_VERSION_DEACTIVATED"
                and e.payload.get("institution_id") == institution_id
                and e.payload.get("model_version") == model_version
                for e in folded
            )
            reasons.append("版本已被机构停用" if deactivated else "机构未登记该模型版本")
        else:
            ok, reason = reg.status(disease_code, at, adverse)
            if not ok:
                reasons.append(reason)
        return {
            "institution_id": institution_id,
            "institution_name": inst.name,
            "model_version": model_version,
            "disease_code": disease_code,
            "evaluated_at": at,
            "adverse_events": adverse,
            "permitted": not reasons,
            "reasons": reasons,
        }


class RegistryAdmin:
    """机构登记变更；每次变更都是不可修改的事件。"""

    def __init__(self, registry, log, roles=None):
        self._registry = registry
        self._log = log
        self._roles = roles

    def register_version(self, *, institution_id, registration, at, by,
                         by_role="org_admin", event_id=None):
        if self._roles:
            self._roles.require(by_role, "registry:manage")
        self._registry.institution(institution_id)
        parsed = parse_registration(registration)
        payload = {
            "institution_id": institution_id,
            "registration": {
                "model_version": parsed.model_version,
                "diseases": list(parsed.diseases),
                "effective_from": parsed.effective_from,
                "deactivate_if": {
                    "after": parsed.deactivate_if.after,
                    "adverse_events_at_least": parsed.deactivate_if.adverse_events_at_least,
                },
            },
        }
        return self._log.append(
            event_id=event_id or "reg-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="MODEL_VERSION_REGISTERED", occurred_at=at, recorded_at=at,
            actor=by, payload=payload)

    def deactivate_version(self, *, institution_id, model_version, reason, at, by,
                           by_role="org_admin", event_id=None):
        if self._roles:
            self._roles.require(by_role, "registry:manage")
        self._registry.institution(institution_id)
        payload = {"institution_id": institution_id,
                   "model_version": model_version, "reason": reason}
        return self._log.append(
            event_id=event_id or "deact-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="MODEL_VERSION_DEACTIVATED", occurred_at=at, recorded_at=at,
            actor=by, payload=payload)
