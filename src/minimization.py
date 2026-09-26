"""推理输入的最小化与脱敏：只保留最少必要特征。"""

from __future__ import annotations

from .errors import DirectIdentifierError, ValidationError

FEATURE_ALLOWLIST = frozenset({
    "disease_code", "pattern_code", "symptoms", "tongue", "pulse",
    "age_band", "sex", "course_days",
})

DIRECT_IDENTIFIERS = frozenset({
    "name", "id_number", "phone", "address", "email",
    "medical_record_no", "insurance_no", "face_image",
})

REQUIRED = ("pattern_code",)


def minimize_features(raw: dict) -> dict:
    """拒绝直接标识，丢弃白名单外字段，返回确定序的最小特征集。"""
    if not isinstance(raw, dict):
        raise ValidationError("特征必须为对象")
    found = sorted(set(raw) & DIRECT_IDENTIFIERS)
    if found:
        raise DirectIdentifierError(f"请求含直接标识字段: {found}")
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise ValidationError(f"缺少必要特征: {missing}")
    return {k: raw[k] for k in sorted(raw) if k in FEATURE_ALLOWLIST}


def validate_features(features: dict, terminology) -> None:
    disease = features.get("disease_code")
    if disease and not terminology.has_disease(disease):
        raise ValidationError(f"未知病种编码: {disease}")
    pattern = features.get("pattern_code")
    if pattern and not terminology.has_pattern(pattern):
        raise ValidationError(f"未知证型编码: {pattern}")
