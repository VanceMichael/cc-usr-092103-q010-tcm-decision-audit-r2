"""模型版本注册表：确定性规则桩，输出携带名医经验来源。

同一版本对同一输入永远给出同一输出；不同版本的规则表不同，
这正是"同一病例在不同版本下建议不同"的可解释来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class Rule:
    disease_code: str
    pattern_code: str
    formula_code: str
    experience_code: str
    experience_version: int


@dataclass(frozen=True)
class ModelVersion:
    version: str
    released_at: str
    rules: tuple


class ModelRegistry:
    def __init__(self, versions, terminology):
        self._terminology = terminology
        self._versions = {}
        for mv in versions:
            self._versions[mv.version] = {(r.disease_code, r.pattern_code): r for r in mv.rules}

    @classmethod
    def load(cls, path, terminology) -> "ModelRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "models" not in data:
            raise ValidationError("模型资料缺少 models 字段")
        versions = []
        for raw in data["models"]:
            if not isinstance(raw, dict) or not {"version", "released_at", "rules"} <= set(raw):
                raise ValidationError(f"模型条目缺少必要字段: {raw!r}")
            rules = []
            for r in raw["rules"]:
                rule = Rule(
                    disease_code=r["disease_code"],
                    pattern_code=r["pattern_code"],
                    formula_code=r["formula_code"],
                    experience_code=r["experience_code"],
                    experience_version=int(r["experience_version"]),
                )
                if not terminology.has_disease(rule.disease_code):
                    raise ValidationError(f"规则引用未知病种: {rule.disease_code}")
                if not terminology.has_pattern(rule.pattern_code):
                    raise ValidationError(f"规则引用未知证型: {rule.pattern_code}")
                if not terminology.has_formula(rule.formula_code):
                    raise ValidationError(f"规则引用未知方剂: {rule.formula_code}")
                terminology.experience(rule.experience_code, rule.experience_version)
                rules.append(rule)
            versions.append(ModelVersion(version=raw["version"],
                                         released_at=raw["released_at"],
                                         rules=tuple(rules)))
        return cls(versions, terminology)

    def versions(self) -> tuple:
        return tuple(self._versions)

    def has(self, version) -> bool:
        return version in self._versions

    def recommend(self, version: str, features: dict) -> dict:
        if version not in self._versions:
            raise NotFoundError(f"未知模型版本: {version}")
        key = (features.get("disease_code"), features.get("pattern_code"))
        rule = self._versions[version].get(key)
        if rule is None:
            return {"status": "no_rule", "formula_code": None, "formula_name": None,
                    "experience_code": None, "experience_version": None,
                    "experience_name": None, "rationale": "无匹配规则"}
        formula = self._terminology.formula(rule.formula_code)
        experience = self._terminology.experience(rule.experience_code, rule.experience_version)
        return {"status": "ok", "formula_code": formula.code, "formula_name": formula.name,
                "experience_code": experience.code, "experience_version": experience.version,
                "experience_name": experience.name, "rationale": experience.summary}
