"""审计重放：按决策当时的权限、授权、病种范围、经验来源与输入重建过程。

回答"同一病例为何在两个模型版本下得到不同建议"：
重放只依赖事件日志与术语资料，重建当时的机构权限、患者授权、
获准病种、名医经验来源与实际输入，重新解析版本并重跑模型，
并把模型输出与医生最终处置分开呈现。
"""

from __future__ import annotations

from .disposition import DispositionService
from .errors import NotFoundError
from .gray import resolve_version, rules_at


class ReplayService:
    def __init__(self, *, log, registry, terminology, models, consent):
        self._log = log
        self._registry = registry
        self._terminology = terminology
        self._models = models
        self._consent = consent

    def decision(self, decision_id) -> dict:
        for e in self._log.of_type("INFERENCE_COMPLETED"):
            if e.payload.get("decision_id") == decision_id:
                return e.payload
        raise NotFoundError(f"未知决策: {decision_id}")

    def replay(self, decision_id) -> dict:
        record = self.decision(decision_id)
        at = record["occurred_at"]
        events_at = self._log.up_to(at)

        consent = self._consent.state_at(record["patient_pseudonym"], at)
        consent_snapshot = {"purposes": consent["purposes"],
                            "share_scope": consent["share_scope"]}
        permission = self._registry.permission_snapshot(
            record["institution_id"], record["model_version"],
            record["features"]["disease_code"], at, events_at)
        candidates = self._registry.candidate_versions(
            record["institution_id"], at, events_at)
        rules, rules_event = rules_at(events_at, record["institution_id"], at)
        version, gray = resolve_version(institution_id=record["institution_id"],
                                        request_id=record["request_id"],
                                        candidates=candidates, rules=rules, at=at)
        gray["rules_event"] = rules_event
        output = self._models.recommend(record["model_version"], record["features"])

        checks = {
            "model_output_match": output == record["model_output"],
            "version_match": version == record["model_version"],
            "permission_match": permission == record["permission"],
            "consent_match": consent_snapshot == record["consent"],
            "gray_match": gray == record["gray"],
            "chain_intact": self._log.verify(),
        }
        disposition = DispositionService(self._log).disposition_for(decision_id)
        return {
            "decision_id": decision_id,
            "occurred_at": at,
            "institution_id": record["institution_id"],
            "input": record["features"],
            "model_version": record["model_version"],
            "model_output": record["model_output"],      # 模型输出
            "doctor_disposition": disposition,            # 医生最终处置（与模型输出分列）
            "context": {"permission": permission, "consent": consent_snapshot,
                        "gray": gray},
            "checks": checks,
            "consistent": all(checks.values()),
        }

    def explain_difference(self, decision_id_a, decision_id_b) -> dict:
        """结构化解释两次决策的差异来源。"""
        ra, rb = self.replay(decision_id_a), self.replay(decision_id_b)
        reasons = []
        if ra["institution_id"] != rb["institution_id"]:
            reasons.append({
                "kind": "institution",
                "detail": f"机构不同（{ra['institution_id']} / {rb['institution_id']}），"
                          "登记版本与适用范围可能不同",
            })
        if ra["model_version"] != rb["model_version"]:
            out_a, out_b = ra["model_output"], rb["model_output"]
            reasons.append({
                "kind": "model_version",
                "detail": f"模型版本不同（{ra['model_version']} / {rb['model_version']}）",
                "experience_a": f"{out_a.get('experience_name')} v{out_a.get('experience_version')}",
                "experience_b": f"{out_b.get('experience_name')} v{out_b.get('experience_version')}",
            })
        if ra["input"] != rb["input"]:
            keys = sorted(set(ra["input"]) | set(rb["input"]))
            diff = {k: [ra["input"].get(k), rb["input"].get(k)]
                    for k in keys if ra["input"].get(k) != rb["input"].get(k)}
            reasons.append({"kind": "input", "detail": "实际输入特征不同", "diff": diff})
        perm_a = ra["context"]["permission"]
        perm_b = rb["context"]["permission"]
        if (perm_a["permitted"], perm_a["reasons"]) != (perm_b["permitted"], perm_b["reasons"]):
            reasons.append({"kind": "permission",
                            "detail": "机构权限或获准病种状态不同",
                            "a": perm_a["reasons"], "b": perm_b["reasons"]})
        if ra["context"]["consent"] != rb["context"]["consent"]:
            reasons.append({"kind": "consent", "detail": "患者授权状态不同",
                            "a": ra["context"]["consent"], "b": rb["context"]["consent"]})
        if ra["context"]["gray"].get("matched_rule") != rb["context"]["gray"].get("matched_rule"):
            reasons.append({"kind": "gray", "detail": "灰度规则命中情况不同",
                            "a": ra["context"]["gray"].get("matched_rule"),
                            "b": rb["context"]["gray"].get("matched_rule")})
        if not reasons and ra["model_output"] != rb["model_output"]:
            reasons.append({"kind": "output",
                            "detail": "上下文一致但输出不同，需核查日志完整性"})
        return {
            "a": {"decision_id": decision_id_a, "model_version": ra["model_version"],
                  "model_output": ra["model_output"],
                  "doctor_disposition": ra["doctor_disposition"]},
            "b": {"decision_id": decision_id_b, "model_version": rb["model_version"],
                  "model_output": rb["model_output"],
                  "doctor_disposition": rb["doctor_disposition"]},
            "reasons": reasons,
            "note": "模型输出与医生最终处置分别列出；临床效力以医生确认内容为准。",
        }
