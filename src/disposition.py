"""医生处置：采用、驳回、修订与不良结果上报，均为不可修改事件。

模型输出与医生最终处置分开记录：最终处置一旦写入不可更改，
重复提交同一处置幂等返回，不同内容则拒绝。
"""

from __future__ import annotations

from .errors import ConflictError, ValidationError
from .util import canonical_json, sha256_hex

FINAL_EVENTS = {
    "DISPOSITION_CONFIRMED": "confirmed",
    "DISPOSITION_REJECTED": "rejected",
    "DISPOSITION_REVISED": "revised",
}


class DispositionService:
    def __init__(self, log, terminology=None, roles=None):
        self._log = log
        self._terminology = terminology
        self._roles = roles

    def confirm(self, *, decision_id, doctor_id, at, note="", doctor_role="physician"):
        if self._roles:
            self._roles.require(doctor_role, "dispose:own")
        return self._finalize(decision_id=decision_id, doctor_id=doctor_id, at=at,
                              event_type="DISPOSITION_CONFIRMED", extra={"note": note})

    def reject(self, *, decision_id, doctor_id, at, reason, doctor_role="physician"):
        if self._roles:
            self._roles.require(doctor_role, "dispose:own")
        if not reason:
            raise ValidationError("驳回必须填写理由")
        return self._finalize(decision_id=decision_id, doctor_id=doctor_id, at=at,
                              event_type="DISPOSITION_REJECTED", extra={"reason": reason})

    def revise(self, *, decision_id, doctor_id, at, new_formula_code, note="",
               doctor_role="physician"):
        if self._roles:
            self._roles.require(doctor_role, "dispose:own")
        if self._terminology is not None and not self._terminology.has_formula(new_formula_code):
            raise ValidationError(f"未知方剂编码: {new_formula_code}")
        return self._finalize(decision_id=decision_id, doctor_id=doctor_id, at=at,
                              event_type="DISPOSITION_REVISED",
                              extra={"new_formula_code": new_formula_code, "note": note})

    def report_adverse(self, *, decision_id, institution_id, model_version, doctor_id,
                       at, description, severity, doctor_role="physician", recorded_at=None):
        if self._roles:
            self._roles.require(doctor_role, "adverse:report")
        if not description:
            raise ValidationError("不良结果必须填写描述")
        payload = {"decision_id": decision_id, "institution_id": institution_id,
                   "model_version": model_version, "description": description,
                   "severity": severity}
        return self._log.append(
            event_id="adv-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="ADVERSE_OUTCOME_REPORTED", occurred_at=at,
            recorded_at=recorded_at or at, actor=doctor_id, payload=payload)

    def disposition_for(self, decision_id) -> dict:
        """处置投影：最终状态 + 不良结果上报列表。"""
        final = None
        adverse = []
        for e in self._log.events:
            payload = e.payload
            if payload.get("decision_id") != decision_id:
                continue
            if e.type in FINAL_EVENTS and final is None:
                final = e
            elif e.type == "ADVERSE_OUTCOME_REPORTED":
                adverse.append({"at": e.occurred_at, "by": e.actor,
                                "description": payload["description"],
                                "severity": payload["severity"]})
        if final is None:
            return {"decision_id": decision_id, "status": "pending",
                    "final_formula_code": None, "doctor": None, "at": None,
                    "note": "", "reason": None, "adverse_reports": adverse}
        return {"decision_id": decision_id, "status": FINAL_EVENTS[final.type],
                "final_formula_code": final.payload.get("new_formula_code"),
                "doctor": final.actor, "at": final.occurred_at,
                "note": final.payload.get("note", ""),
                "reason": final.payload.get("reason"),
                "adverse_reports": adverse}

    def _finalize(self, *, decision_id, doctor_id, at, event_type, extra):
        payload = {"decision_id": decision_id, "doctor_id": doctor_id, **extra}
        for e in self._log.events:
            if e.type in FINAL_EVENTS and e.payload.get("decision_id") == decision_id:
                if e.type == event_type and e.payload == payload:
                    return e  # 重复提交幂等
                raise ConflictError(f"决策 {decision_id} 已有最终处置，不可更改")
        return self._log.append(
            event_id="disp-" + sha256_hex(canonical_json(payload))[:16],
            type=event_type, occurred_at=at, recorded_at=at,
            actor=doctor_id, payload=payload)
