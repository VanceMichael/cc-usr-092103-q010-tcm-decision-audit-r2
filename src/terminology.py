"""方证术语：证型、方剂、病种与名医经验来源的读取与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class Pattern:
    code: str
    name: str


@dataclass(frozen=True)
class Formula:
    code: str
    name: str


@dataclass(frozen=True)
class Disease:
    code: str
    name: str


@dataclass(frozen=True)
class Experience:
    code: str
    name: str
    version: int
    effective_from: str
    summary: str


def _entries(data, key, fields):
    rows = []
    for raw in data[key]:
        if not isinstance(raw, dict) or not set(fields) <= set(raw):
            raise ValidationError(f"{key} 条目缺少字段 {sorted(fields)}: {raw!r}")
        rows.append(raw)
    return rows


class Terminology:
    """按编码检索术语；名医经验按 (编码, 版本) 检索。"""

    def __init__(self, *, patterns, formulas, diseases, experiences):
        self._patterns = {p.code: p for p in patterns}
        self._formulas = {f.code: f for f in formulas}
        self._diseases = {d.code: d for d in diseases}
        self._experiences = {(e.code, e.version): e for e in experiences}

    @classmethod
    def load(cls, path) -> "Terminology":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"domain", "version", "patterns", "formulas", "diseases", "experiences"}
        missing = required - set(data)
        if missing:
            raise ValidationError(f"术语资料缺少字段: {sorted(missing)}")
        patterns = [Pattern(code=r["code"], name=r["name"])
                    for r in _entries(data, "patterns", {"code", "name"})]
        formulas = [Formula(code=r["code"], name=r["name"])
                    for r in _entries(data, "formulas", {"code", "name"})]
        diseases = [Disease(code=r["code"], name=r["name"])
                    for r in _entries(data, "diseases", {"code", "name"})]
        experiences = [
            Experience(code=r["code"], name=r["name"], version=int(r["version"]),
                       effective_from=r["effective_from"], summary=r["summary"])
            for r in _entries(data, "experiences",
                              {"code", "name", "version", "effective_from", "summary"})
        ]
        return cls(patterns=patterns, formulas=formulas,
                   diseases=diseases, experiences=experiences)

    def pattern(self, code) -> Pattern:
        try:
            return self._patterns[code]
        except KeyError:
            raise NotFoundError(f"未知证型编码: {code}") from None

    def formula(self, code) -> Formula:
        try:
            return self._formulas[code]
        except KeyError:
            raise NotFoundError(f"未知方剂编码: {code}") from None

    def disease(self, code) -> Disease:
        try:
            return self._diseases[code]
        except KeyError:
            raise NotFoundError(f"未知病种编码: {code}") from None

    def experience(self, code, version=None) -> Experience:
        if version is None:
            candidates = [v for (c, v) in self._experiences if c == code]
            if not candidates:
                raise NotFoundError(f"未知名医经验: {code}")
            version = max(candidates)
        try:
            return self._experiences[(code, version)]
        except KeyError:
            raise NotFoundError(f"未知名医经验: {code} v{version}") from None

    def has_pattern(self, code) -> bool:
        return code in self._patterns

    def has_formula(self, code) -> bool:
        return code in self._formulas

    def has_disease(self, code) -> bool:
        return code in self._diseases
