"""科研导出管控。

四道闸：
1. 授权用途必须含 research，且伦理批件、数据集在授权 research_scope 内；
2. 授权已撤回或不含再标识许可时，一律拒绝再标识，研究化名使用
   独立盐值，与临床化名不可互推；
3. 输出仅含术语编码与粗粒度准标识符，并做 k-匿名抑制；
4. 每次导出（含拒绝）本身留痕。
"""

from collections import Counter

from .consents import PURPOSE_RESEARCH
from .engine import AuditEngine
from .serde import parse_dt, pseudonym

RESEARCH_SALT = "salt-research-separate-2026"
DEFAULT_K = 3

# 数据集目录：数据集只覆盖已登记病种，导出行按此过滤。
DATASET_CATALOG = {
    "DS-COPD-OUTCOME": {"disease_codes": ["DIS-COPD"]},
}


class ResearchDenied(PermissionError):
    pass


class ResearchExport:
    def __init__(self, engine: AuditEngine):
        self.engine = engine

    def export_dataset(self, *, dataset: str, ethics_ref: str, actor: dict,
                       k: int = DEFAULT_K) -> dict:
        self.engine.roles.require(actor["role"], "PERM-RESEARCH-EXPORT")
        if dataset not in DATASET_CATALOG:
            raise ResearchDenied(f"数据集 {dataset} 未登记目录")
        # 伦理批件必须曾在科研授权中登记；逐行校验再处理撤回/范围等问题，
        # 保证“合规伦理但病例全部不可导出”时仍留下 0 行导出事件。
        ethics_known = any(
            (g.get("research_scope") or {}).get("ethics_ref") == ethics_ref
            for g in self.engine.consents._grants.values())  # noqa: SLF001
        if not ethics_known:
            raise ResearchDenied(f"伦理批件 {ethics_ref} 未在任何科研授权中登记")
        in_scope_diseases = set(DATASET_CATALOG[dataset]["disease_codes"])
        rows, skipped = [], []

        for d in self.engine._decisions.values():  # noqa: SLF001 - 同包审计装配
            if d.model_output is None or d.disposition is None:
                continue  # 仅导出已形成医生处置的病例
            if d.disposition["action"] not in ("confirmed", "revised"):
                continue  # 医生驳回（不采纳）不作为处置结局导出
            if d.request["disease_code"] not in in_scope_diseases:
                continue
            consent_id = d.request.get("consent_id")
            try:
                grant = self.engine.consents.grant(consent_id)
                if self.engine.consents.is_withdrawn(consent_id):
                    raise ResearchDenied(
                        f"授权 {consent_id} 已撤回"
                        f"（{self.engine.consents._withdrawn[consent_id]}）")
                self._check_scope(grant, dataset, ethics_ref, d.occurred_at)
            except (ResearchDenied, KeyError) as exc:
                skipped.append({"request_uid": d.request["request_uid"], "reason": str(exc)})
                continue

            disposition = d.disposition
            final_codes = (disposition.get("final_formula_codes")
                           or d.model_output["recommended_formula_codes"])
            features = d.request["features"]
            rows.append({
                "research_pseudonym": pseudonym(
                    d.request.get("patient_pseudonym", ""), RESEARCH_SALT, prefix="R"),
                "disease_code": d.request["disease_code"],
                "syndrome_code": features["syndrome_code"],
                "final_formula_codes": final_codes,
                "doctor_action": disposition["action"],
                "model_version": d.model_output["model_version"],
                "age_band": features.get("age_band", "U"),
                "sex": features.get("sex", "U"),
                "visit_month": d.occurred_at[:7],
                # 不含机构名、医生化名、临床患者化名、时间戳精确值
            })

        rows, suppressed_groups = self._k_anonymize(rows, k)
        result = {"dataset": dataset, "ethics_ref": ethics_ref, "k": k,
                  "rows": rows, "included": len(rows), "suppressed_groups": suppressed_groups,
                  "skipped": skipped,
                  "reidentification_allowed": False}
        self.engine.events.append(
            "RESEARCH_EXPORT",
            {"dataset": dataset, "ethics_ref": ethics_ref, "included": len(rows),
             "k": k, "suppressed_groups": suppressed_groups, "skipped_count": len(skipped),
             "reidentification_allowed": False},
            occurred_at=result_rows_occurred_at(rows),
            actor=actor, org_id=None)
        return result

    def deny_export(self, *, dataset: str, ethics_ref: str, actor: dict,
                    reason: str, at: str) -> dict:
        """不合规的导出尝试同样留痕。"""
        self.engine.events.append(
            "RESEARCH_EXPORT_DENIED",
            {"dataset": dataset, "ethics_ref": ethics_ref, "reason": reason},
            occurred_at=at, actor=actor, org_id=None)
        return {"denied": True, "reason": reason}

    @staticmethod
    def _check_scope(grant: dict, dataset: str, ethics_ref: str, occurred_at: str) -> None:
        if grant.get("status") == "withdrawn" or PURPOSE_RESEARCH not in grant["purposes"]:
            raise ResearchDenied(f"授权 {grant['consent_id']} 不含有效科研用途")
        scope = grant.get("research_scope")
        if not scope:
            raise ResearchDenied(f"授权 {grant['consent_id']} 未限定科研范围")
        if scope["ethics_ref"] != ethics_ref:
            raise ResearchDenied("伦理批件与授权登记不一致")
        if dataset not in scope["datasets"]:
            raise ResearchDenied(f"数据集 {dataset} 超出授权范围 {scope['datasets']}")
        if scope.get("reidentification_allowed"):
            raise ResearchDenied("授权不得允许再标识")
        at = parse_dt(occurred_at)
        if not (parse_dt(grant["granted_on"]) <= at <= parse_dt(grant["valid_until"])):
            raise ResearchDenied("导出病例的就诊时间不在科研授权有效期内")

    @staticmethod
    def _k_anonymize(rows: list[dict], k: int) -> tuple[list[dict], list[dict]]:
        """对 (age_band, sex) 准标识符组合做计数与抑制。"""
        counts = Counter((r["age_band"], r["sex"]) for r in rows)
        suppressed = [{"quasi": list(q), "count": c}
                      for q, c in counts.items() if c < k]
        small = {q for q, c in counts.items() if c < k}
        for row in rows:
            if (row["age_band"], row["sex"]) in small:
                row["age_band"], row["sex"] = "*", "*"
        return rows, suppressed


def result_rows_occurred_at(rows: list[dict]) -> str:
    months = sorted(r["visit_month"] for r in rows)
    return (months[0] + "-01T00:00:00+08:00") if months else "2026-09-26T00:00:00+08:00"
