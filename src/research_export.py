"""科研导出：不得越过用途授权，不得还原个人。

- 只纳入导出时刻仍持有对应用途授权的患者（撤回即排除）；
- 患者以密钥派生的不可逆伪名标识，密钥不随导出提供；
- 时间为月粒度、机构只保留层级类型，行内不出现任何
  直接标识或临床系统内部标识。
"""

from __future__ import annotations

import hashlib
import hmac

from .disposition import DispositionService
from .errors import ValidationError
from .minimization import DIRECT_IDENTIFIERS
from .util import parse_ts

FORBIDDEN_FIELDS = DIRECT_IDENTIFIERS | {
    "request_id", "decision_id", "institution_id", "patient_pseudonym",
    "doctor_id", "model_version", "recorded_at",
}


class ResearchExporter:
    def __init__(self, *, log, consent, registry, key: bytes, roles=None):
        self._log = log
        self._consent = consent
        self._registry = registry
        self._key = bytes(key)
        self._roles = roles

    def export(self, *, at, purpose="research", by_role="researcher") -> list:
        if self._roles:
            self._roles.require(by_role, "research:export")
        dispositions = DispositionService(self._log)
        rows = []
        for e in self._log.of_type("INFERENCE_COMPLETED"):
            record = e.payload
            if parse_ts(record["occurred_at"]) > parse_ts(at):
                continue
            if not self._consent.allows(record["patient_pseudonym"], purpose, at):
                continue  # 未授权或已撤回用途
            disposition = dispositions.disposition_for(record["decision_id"])
            features = record["features"]
            if disposition["status"] == "revised":
                final_formula = disposition["final_formula_code"]
            elif disposition["status"] == "confirmed":
                final_formula = record["model_output"].get("formula_code")
            else:
                final_formula = None
            row = {
                "row": len(rows) + 1,
                "patient_key": hmac.new(self._key,
                                        record["patient_pseudonym"].encode("utf-8"),
                                        hashlib.sha256).hexdigest(),
                "occurred_month": record["occurred_at"][:7],
                "institution_tier": self._registry.institution(record["institution_id"]).tier,
                "disease_code": features.get("disease_code"),
                "pattern_code": features.get("pattern_code"),
                "age_band": features.get("age_band"),
                "sex": features.get("sex"),
                "doctor_confirmed": disposition["status"] in ("confirmed", "revised"),
                "final_formula_code": final_formula,
                "adverse_reported": bool(disposition["adverse_reports"]),
            }
            self._assert_deidentified(row)
            rows.append(row)
        return rows

    @staticmethod
    def _assert_deidentified(row) -> None:
        leaked = sorted(set(row) & FORBIDDEN_FIELDS)
        if leaked:
            raise ValidationError(f"导出行包含禁止字段: {leaked}")
