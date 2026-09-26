"""患者端视图：只显示医生确认后的内容。

模型原始建议、待主治确认的请求、被医生驳回（不采纳）的建议一律
不可见；修订后显示的是医生最终方剂而非模型原方案。住院医发起、
尚未经主治确认的请求同样不可见。
"""

from .engine import AuditEngine

# 仅这两种处置状态代表“医生已确认并对患者负责”的内容。
VISIBLE_STATUS = {"confirmed", "revised"}


class PatientDenied(PermissionError):
    pass


class PatientPortal:
    def __init__(self, engine: AuditEngine):
        self.engine = engine

    def view(self, patient_pseudonym: str, requester: dict) -> list[dict]:
        if requester.get("role") != "ROLE-PATIENT":
            raise PatientDenied("仅患者本人可通过患者端查看")
        if requester.get("pseudonym") != patient_pseudonym:
            raise PatientDenied("化名不匹配，禁止调阅他人记录")

        visible = []
        terms = self.engine.terminology
        for d in self.engine.decisions_for_patient(patient_pseudonym):
            if d.status not in VISIBLE_STATUS or d.disposition is None:
                continue
            if d.disposition["action"] == "revised":
                final_codes = d.disposition["final_formula_codes"]
            else:
                final_codes = d.model_output["recommended_formula_codes"]
            visible.append({
                "org_id": d.request["org_id"],
                "visit_occurred_at": d.occurred_at,
                "disease": terms.diseases[d.request["disease_code"]]["name"],
                "syndrome": terms.syndromes[d.request["features"]["syndrome_code"]]["name"],
                "treatment_principle": [
                    terms.principles[c]["name"]
                    for c in d.model_output["treatment_principle_codes"]],
                "doctor_final_formulas": [terms.formulas[c]["name"] for c in final_codes],
                "doctor_note": d.disposition.get("note", ""),
                "confirmed_by_doctor": True,
                # 患者端明确告知：这是医生确认的处置，不展示模型原始输出。
                "source": "医生确认的最终处置",
            })
        visible.sort(key=lambda x: x["visit_occurred_at"])
        return visible
