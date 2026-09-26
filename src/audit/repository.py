"""装载 data/ 与 fixtures/ 下的领域资料。"""

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Repository:
    """内存只读资料仓；所有资料带 schema 版本字段。"""

    def __init__(self, data_dir: Path | None = None, fixture_dir: Path | None = None):
        self.data_dir = data_dir or DATA
        self.fixture_dir = fixture_dir or FIXTURES
        self.terms = _read(self.data_dir / "formulary_terms.json")
        self.orgs_doc = _read(self.data_dir / "organizations.json")
        self.roles_doc = _read(self.data_dir / "roles.json")
        self.registry_doc = _read(self.data_dir / "model_registry.json")
        self.consents_doc = _read(self.data_dir / "consents.json")

    @property
    @lru_cache(maxsize=1)  # noqa: B019 - 只读缓存
    def organizations(self) -> dict:
        return {o["org_id"]: o for o in self.orgs_doc["organizations"]}

    @property
    @lru_cache(maxsize=1)
    def roles(self) -> dict:
        return {r["code"]: r for r in self.roles_doc["roles"]}

    @property
    @lru_cache(maxsize=1)
    def models(self) -> dict:
        return {m["model_uid"]: m for m in self.registry_doc["models"]}

    @property
    @lru_cache(maxsize=1)
    def registrations(self) -> dict:
        return {(r["org_id"], r["model_uid"]): r for r in self.registry_doc["registrations"]}

    @property
    @lru_cache(maxsize=1)
    def patients(self) -> dict:
        return {p["pseudonym"]: p for p in self.consents_doc["patients"]}

    def index_terms(self, key: str) -> dict:
        return {item["code"]: item for item in self.terms[key]}
