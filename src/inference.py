"""推理服务：最小化输入、确定版本、留存可重放的决策记录。

- 重复请求：request_id + 内容指纹幂等，重试得到同一记录；
- 离线补传：occurred_at 可以是过去时间，快照按发生时间折叠；
- 每次成功或拒绝都写入不可变事件，决策记录完整携带当时
  的机构权限、患者授权、灰度解析与实际输入。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .errors import ConflictError, ConsentError, NotFoundError, PermissionDenied
from .gray import resolve_version, rules_at
from .minimization import minimize_features, validate_features
from .util import canonical_json, sha256_hex


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    request_id: str
    request_hash: str
    institution_id: str
    doctor_id: str
    patient_pseudonym: str
    occurred_at: str
    recorded_at: str
    offline: bool
    features: dict
    model_version: str
    model_output: dict
    permission: dict
    consent: dict
    gray: dict

    def to_dict(self) -> dict:
        return asdict(self)


class InferenceService:
    def __init__(self, *, registry, terminology, models, log, consent, roles=None):
        self._registry = registry
        self._terminology = terminology
        self._models = models
        self._log = log
        self._consent = consent
        self._roles = roles
        self._by_request: dict[str, DecisionRecord] = {}
        for event in self._log.of_type("INFERENCE_COMPLETED"):
            payload = event.payload
            self._by_request[payload["request_id"]] = DecisionRecord(**payload)

    def infer(self, *, request_id, institution_id, doctor_id, patient_pseudonym,
              disease_code, features, at, recorded_at=None, offline=False,
              doctor_role="physician") -> DecisionRecord:
        recorded_at = recorded_at or at
        if self._roles:
            self._roles.require(doctor_role, "infer")
        fingerprint = sha256_hex(canonical_json({
            "institution_id": institution_id,
            "doctor_id": doctor_id,
            "patient_pseudonym": patient_pseudonym,
            "disease_code": disease_code,
            "features": features,
            "at": at,
            "offline": offline,
        }))
        existing = self._by_request.get(request_id)
        if existing is not None:
            if existing.request_hash == fingerprint:
                return existing  # 重复请求得到确定结果
            raise ConflictError(f"请求 {request_id} 已存在且内容不同")

        minimized = minimize_features(features)
        model_input = {"disease_code": disease_code, **minimized}
        validate_features(model_input, self._terminology)

        consent_state = self._consent.state_at(patient_pseudonym, at)
        if "clinical" not in consent_state["purposes"]:
            self._deny(request_id=request_id, institution_id=institution_id,
                       doctor_id=doctor_id, reason="患者未授权临床辅助用途",
                       at=at, recorded_at=recorded_at)
            raise ConsentError("患者未授权临床辅助用途")

        events_at = self._log.up_to(at)
        candidates = self._registry.candidate_versions(institution_id, at, events_at)
        rules, rules_event = rules_at(events_at, institution_id, at)
        try:
            version, gray = resolve_version(institution_id=institution_id,
                                            request_id=request_id,
                                            candidates=candidates, rules=rules, at=at)
        except NotFoundError:
            self._deny(request_id=request_id, institution_id=institution_id,
                       doctor_id=doctor_id, reason="机构无可用模型版本",
                       at=at, recorded_at=recorded_at)
            raise PermissionDenied("机构无可用模型版本") from None
        gray["rules_event"] = rules_event

        permission = self._registry.permission_snapshot(
            institution_id, version, disease_code, at, events_at)
        if not permission["permitted"]:
            reason = "；".join(permission["reasons"])
            self._deny(request_id=request_id, institution_id=institution_id,
                       doctor_id=doctor_id, reason=reason, at=at, recorded_at=recorded_at)
            raise PermissionDenied(reason)

        output = self._models.recommend(version, model_input)
        record = DecisionRecord(
            decision_id=f"dec-{request_id}",
            request_id=request_id,
            request_hash=fingerprint,
            institution_id=institution_id,
            doctor_id=doctor_id,
            patient_pseudonym=patient_pseudonym,
            occurred_at=at,
            recorded_at=recorded_at,
            offline=offline,
            features=model_input,
            model_version=version,
            model_output=output,
            permission=permission,
            consent={"purposes": consent_state["purposes"],
                     "share_scope": consent_state["share_scope"]},
            gray=gray,
        )
        self._by_request[request_id] = record
        self._log.append(event_id=f"inf-{request_id}", type="INFERENCE_COMPLETED",
                         occurred_at=at, recorded_at=recorded_at, actor=doctor_id,
                         payload=record.to_dict())
        return record

    def record(self, request_id):
        return self._by_request.get(request_id)

    def _deny(self, *, request_id, institution_id, doctor_id, reason, at, recorded_at):
        payload = {"request_id": request_id, "institution_id": institution_id,
                   "doctor_id": doctor_id, "reason": reason}
        suffix = sha256_hex(canonical_json(payload))[:12]
        self._log.append(event_id=f"denied-{request_id}-{suffix}",
                         type="INFERENCE_DENIED", occurred_at=at,
                         recorded_at=recorded_at, actor=doctor_id, payload=payload)
