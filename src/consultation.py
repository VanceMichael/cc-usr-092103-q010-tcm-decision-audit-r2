"""跨院会诊：共享仅限脱敏特征，结论唯一且确定。"""

from __future__ import annotations

from .errors import ConsentError, NotFoundError, ValidationError


class ConsultationService:
    def __init__(self, log, consent, registry, roles=None):
        self._log = log
        self._consent = consent
        self._registry = registry
        self._roles = roles

    def request(self, *, consultation_id, decision_id, from_institution, to_institution,
                question, at, by, by_role="physician"):
        if self._roles:
            self._roles.require(by_role, "consult:request")
        if from_institution == to_institution:
            raise ValidationError("会诊双方不能为同一机构")
        self._registry.institution(from_institution)
        self._registry.institution(to_institution)
        decision = self._decision(decision_id)
        state = self._consent.state_at(decision["patient_pseudonym"], at)
        if "clinical" not in state["purposes"]:
            raise ConsentError("患者未授权临床辅助用途")
        if state["share_scope"] != "consortium":
            raise ConsentError("患者授权不包含跨院共享")
        payload = {"consultation_id": consultation_id, "decision_id": decision_id,
                   "from_institution": from_institution, "to_institution": to_institution,
                   "question": question, "shared_features": decision["features"]}
        return self._log.append(event_id=f"consul-req-{consultation_id}",
                                type="CONSULTATION_REQUESTED", occurred_at=at,
                                recorded_at=at, actor=by, payload=payload)

    def resolve(self, *, consultation_id, by, at, conclusion, formula_code=None,
                by_role="senior_physician"):
        if self._roles:
            self._roles.require(by_role, "consult:resolve")
        if self._request_event(consultation_id) is None:
            raise NotFoundError(f"未知会诊: {consultation_id}")
        payload = {"consultation_id": consultation_id, "conclusion": conclusion,
                   "formula_code": formula_code}
        # 同一会诊只能有一个结论：重复提交相同结论幂等，不同结论拒绝。
        return self._log.append(event_id=f"consul-res-{consultation_id}",
                                type="CONSULTATION_RESOLVED", occurred_at=at,
                                recorded_at=at, actor=by, payload=payload)

    def status(self, consultation_id) -> dict:
        request = self._request_event(consultation_id)
        if request is None:
            raise NotFoundError(f"未知会诊: {consultation_id}")
        resolved = None
        for e in self._log.of_type("CONSULTATION_RESOLVED"):
            if e.payload.get("consultation_id") == consultation_id:
                resolved = e
                break
        return {"consultation_id": consultation_id,
                "state": "resolved" if resolved else "open",
                "request": request.payload,
                "resolution": resolved.payload if resolved else None,
                "resolved_by": resolved.actor if resolved else None,
                "resolved_at": resolved.occurred_at if resolved else None}

    def _decision(self, decision_id):
        for e in self._log.of_type("INFERENCE_COMPLETED"):
            if e.payload.get("decision_id") == decision_id:
                return e.payload
        raise NotFoundError(f"未知决策: {decision_id}")

    def _request_event(self, consultation_id):
        for e in self._log.of_type("CONSULTATION_REQUESTED"):
            if e.payload.get("consultation_id") == consultation_id:
                return e
        return None
