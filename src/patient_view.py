"""患者端视图：只展示医生确认后的内容。

不暴露模型版本、模型原始输出、名医经验来源与内部标识；
医生驳回或未处置时，患者看不到任何模型建议。
"""

from __future__ import annotations


def patient_view(*, decision: dict, disposition: dict, terminology, registry) -> dict:
    status = disposition["status"]
    if status == "pending":
        return {"status": "pending", "message": "医生尚未确认辅助建议"}
    if status == "rejected":
        return {"status": "none", "message": "无已确认的辅助建议"}
    if status == "confirmed":
        formula_code = decision["model_output"].get("formula_code")
    else:  # revised：展示医生修订后的方剂
        formula_code = disposition["final_formula_code"]
    formula = terminology.formula(formula_code) if formula_code else None
    features = decision["features"]
    institution = registry.institution(decision["institution_id"])
    return {
        "status": "confirmed",
        "disease": terminology.disease(features["disease_code"]).name,
        "pattern": terminology.pattern(features["pattern_code"]).name,
        "formula": formula.name if formula else None,
        "doctor_note": disposition.get("note", ""),
        "institution": institution.name,
        "confirmed_at": disposition["at"],
    }
