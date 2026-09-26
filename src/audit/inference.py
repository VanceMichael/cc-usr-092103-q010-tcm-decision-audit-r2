"""版本化、确定性的方证推理内核。

刻意保持为纯函数：同一版本（含术语版本与经验来源清单）+ 同一
最少必要输入，永远产生同一输出与同一规则轨迹。不含时钟、随机
数或外部调用，因此历史版本可被审计反复重放。

输出始终标注为 model_output（模型建议），是否采纳由医生以
CONFIRMED/REVISED/REJECTED 事件另行处置，二者在审计中严格分离。
"""

from .serde import digest
from .terminology import Terminology

# 每个版本的确定性规则档；变更规则必须发新版本，不得就地改写。
VERSION_PROFILES = {
    "1.4.0": {
        "rule_set": "rules-fz-1.4",
        "score_threshold": 0.5,
        "primary_policy": "defense_first",   # 玉屏风散固表优先
        "unsupported_policy": "no_recommendation",
    },
    "1.5.0": {
        "rule_set": "rules-fz-1.5",
        "score_threshold": 0.6,
        "primary_policy": "spleen_first",    # 四君子汤健脾优先，结合既往方调整
        "unsupported_policy": "no_recommendation",
    },
    "1.6.0-rc1": {
        "rule_set": "rules-fz-1.6-rc",
        "score_threshold": 0.6,
        "primary_policy": "spleen_first",
        "unsupported_policy": "flag_for_review",
    },
}


class InferenceError(RuntimeError):
    pass


def infer(features: dict, disease_code: str, routed: dict,
          terminology: Terminology) -> dict:
    """对已路由确定版本的请求执行推理。"""
    version = routed["version"]
    profile = VERSION_PROFILES.get(version)
    if profile is None:
        raise InferenceError(f"版本 {version} 缺少可重放规则档")
    # 注意：版本是否已停用由路由层在“新请求”路径判断；
    # 重放已停用版本必须仍可执行，故内核不再据此中断。

    syndrome_code = features["syndrome_code"]
    syndrome = terminology.syndromes.get(syndrome_code)
    if syndrome is None:
        raise InferenceError(f"未知证型编码 {syndrome_code}")

    presented = set(features.get("manifestation_codes", []))
    key_marks = set(syndrome["key_manifestation_codes"])
    matched = sorted(presented & key_marks)
    score = round(len(matched) / len(key_marks), 4)
    tongue_hit = features.get("tongue_code") in syndrome["key_manifestation_codes"]
    pulse_hit = features.get("pulse_code") in syndrome["key_manifestation_codes"]
    score = round(min(1.0, score + (0.05 if tongue_hit else 0) + (0.05 if pulse_hit else 0)), 4)

    sources = terminology.experience_for(
        routed["experience_source_codes"], disease_code, syndrome_code)
    source_codes = [s["code"] for s in sources]
    formula_pool: list[str] = []
    for src in sources:
        for fc in src["formula_codes"]:
            if fc not in formula_pool:
                formula_pool.append(fc)

    threshold = profile["score_threshold"]
    supported = score >= threshold and bool(formula_pool)

    if not supported:
        recommended: list[str] = []
        rationale = (f"匹配度 {score} 低于阈值 {threshold} 或本病本证无在册名医经验来源；"
                     f"按 {profile['unsupported_policy']} 不输出方剂建议")
        confidence = 0.0
    else:
        recommended = _rank(formula_pool, profile["primary_policy"], features)
        confidence = _confidence(version, score, len(recommended))
        rationale = _rationale(version, disease_code, syndrome_code, matched,
                               recommended, features, source_codes)

    rule_trace = {
        "rule_set": profile["rule_set"],
        "score_threshold": threshold,
        "primary_policy": profile["primary_policy"],
        "matched_key_manifestations": matched,
        "tongue_hit": tongue_hit,
        "pulse_hit": pulse_hit,
        "match_score": score,
        "experience_source_codes": source_codes,
        "candidate_formula_codes": formula_pool,
    }

    output = {
        "kind": "model_output",
        "model_uid": "model-fz-assist",
        "model_version": version,
        "manifest_digest": routed["manifest_digest"],
        "terminology_version": routed["terminology_version"],
        "disease_code": disease_code,
        "syndrome_code": syndrome_code,
        "recommended_formula_codes": recommended,
        "treatment_principle_codes": syndrome["treatment_principle_codes"],
        "confidence": confidence,
        "supported": supported,
        "rationale": rationale,
        "advisory": "模型建议，仅供中医师参考；不构成处方，最终处置以主治医生确认为准",
        "rule_trace": rule_trace,
    }
    output["output_digest"] = digest({k: v for k, v in output.items()
                                      if k != "output_digest"})
    return output


def _rank(pool: list[str], policy: str, features: dict) -> list[str]:
    if policy == "defense_first":
        order = ["FM-YPGF", "FM-SJZT", "FM-SM"]
    else:  # spleen_first：1.5 起健脾益气前置，固表方置后
        order = ["FM-SJZT", "FM-YPGF", "FM-SM"]
    ranked = [c for c in order if c in pool]
    ranked += [c for c in pool if c not in ranked]
    # 1.5 规则：既往已用且主症仍在的方剂不再重复推荐，避免同一患者连续守方无效。
    prior = set(features.get("prior_formula_codes", []) or [])
    if policy == "spleen_first":
        ranked = [c for c in ranked if c not in prior]
    return ranked


def _confidence(version: str, score: float, n_formulas: int) -> float:
    if version == "1.4.0":
        # 旧版校准偏乐观
        return round(min(0.95, 0.45 + score * 0.5), 4)
    # 1.5 起下调并惩罚多方案不确定性
    return round(max(0.0, 0.35 + score * 0.5 - 0.05 * max(0, n_formulas - 1)), 4)


def _rationale(version, disease, syndrome, matched, recommended, features, sources) -> str:
    names = recommended
    if version == "1.4.0":
        return (f"按{version}固表优先规则，证属{syndrome}，命中主症{len(matched)}项，"
                f"依名医经验{','.join(sources)}建议先固表后健脾：{','.join(names)}")
    prior = features.get("prior_formula_codes") or []
    prior_note = f"；既往已用{','.join(prior)}而主症仍在，调整君药次序" if prior else ""
    return (f"按{version}健脾优先规则，证属{syndrome}，命中主症{len(matched)}项{prior_note}，"
            f"依名医经验{','.join(sources)}建议：{','.join(names)}")
