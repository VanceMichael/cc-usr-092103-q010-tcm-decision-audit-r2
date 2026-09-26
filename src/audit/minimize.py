"""推理请求的最少必要特征校验（数据最小化）。

入库前拒绝任何身份字段与自由文本，只允许术语编码和粗粒度特征。
"""

import re

ALLOWED_TOP_LEVEL = {
    "request_uid", "occurred_at", "org_id", "clinic_role", "clinician_pseudonym",
    "consent_id", "patient_pseudonym", "disease_code", "features",
    "offline", "consultation_id", "consultation_parties", "idempotency_key",
}
ALLOWED_FEATURE_KEYS = {
    "syndrome_code", "manifestation_codes", "tongue_code", "pulse_code",
    "age_band", "sex", "prior_formula_codes",
}
AGE_BANDS = {"<40", "40-59", "60-74", ">=75"}
SEX = {"F", "M", "U"}

# 命中即视为夹带了身份信息或自由文本。
FORBIDDEN_PATTERNS = [
    (re.compile(r"\d{17}[\dXx]"), "身份证号"),
    (re.compile(r"1[3-9]\d{9}"), "手机号"),
    (re.compile(r"姓名|住址|工作单位"), "身份字段名"),
]

_UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{2,63}$")
_CODE_RE = re.compile(r"^[A-Z]+-[A-Z0-9]+$")


class MinimizationError(ValueError):
    pass


def validate_request(req: dict) -> None:
    extra = set(req) - ALLOWED_TOP_LEVEL
    if extra:
        raise MinimizationError(f"请求包含契约外字段：{sorted(extra)}")
    missing = {"request_uid", "occurred_at", "org_id", "clinic_role",
               "consent_id", "disease_code", "features"} - set(req)
    if missing:
        raise MinimizationError(f"请求缺少必要字段：{sorted(missing)}")
    if not _UID_RE.match(req["request_uid"]):
        raise MinimizationError("request_uid 格式不合规")

    features = req["features"]
    if not isinstance(features, dict):
        raise MinimizationError("features 必须为对象")
    extra_f = set(features) - ALLOWED_FEATURE_KEYS
    if extra_f:
        raise MinimizationError(f"features 包含契约外字段：{sorted(extra_f)}")
    if "syndrome_code" not in features or "manifestation_codes" not in features:
        raise MinimizationError("features 缺少 syndrome_code/manifestation_codes")
    if features.get("age_band") and features["age_band"] not in AGE_BANDS:
        raise MinimizationError("age_band 越界")
    if features.get("sex") and features["sex"] not in SEX:
        raise MinimizationError("sex 越界")
    for code_field in ("syndrome_code", "tongue_code", "pulse_code"):
        value = features.get(code_field)
        if value is not None and not _CODE_RE.match(value):
            raise MinimizationError(f"{code_field} 不是术语编码：{value}")
    for list_field in ("manifestation_codes", "prior_formula_codes"):
        for value in features.get(list_field, []) or []:
            if not _CODE_RE.match(value):
                raise MinimizationError(f"{list_field} 含非术语编码：{value}")

    _scan_forbidden(req)


def _scan_forbidden(obj, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(p.search(str(key)) for p, _ in FORBIDDEN_PATTERNS):
                raise MinimizationError(f"{path}.{key} 命中禁止身份字段")
            _scan_forbidden(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_forbidden(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        for pattern, label in FORBIDDEN_PATTERNS:
            if pattern.search(obj):
                raise MinimizationError(f"{path} 疑似夹带{label}，必须在入库前剔除")
