"""测试公共装配。"""

from pathlib import Path
from types import SimpleNamespace

from src.consent import ConsentService
from src.consultation import ConsultationService
from src.disposition import DispositionService
from src.events import EventLog
from src.gray import GrayService
from src.inference import InferenceService
from src.model import ModelRegistry
from src.registry import InstitutionRegistry, RegistryAdmin
from src.replay import ReplayService
from src.research_export import ResearchExporter
from src.roles import Roles
from src.terminology import Terminology

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPORT_KEY = b"test-only-export-key"


def make_system():
    log = EventLog()
    terminology = Terminology.load(FIXTURES / "terminology.json")
    registry = InstitutionRegistry.load(FIXTURES / "institutions.json")
    models = ModelRegistry.load(FIXTURES / "model_versions.json", terminology)
    roles = Roles.load(FIXTURES / "roles.json")
    consent = ConsentService(log)
    inference = InferenceService(registry=registry, terminology=terminology,
                                 models=models, log=log, consent=consent, roles=roles)
    return SimpleNamespace(
        log=log,
        terminology=terminology,
        registry=registry,
        models=models,
        roles=roles,
        consent=consent,
        inference=inference,
        disposition=DispositionService(log, terminology=terminology, roles=roles),
        replay=ReplayService(log=log, registry=registry, terminology=terminology,
                             models=models, consent=consent),
        consult=ConsultationService(log, consent, registry, roles=roles),
        gray=GrayService(log, registry, roles=roles),
        admin=RegistryAdmin(registry, log, roles=roles),
        exporter=ResearchExporter(log=log, consent=consent, registry=registry,
                                  key=EXPORT_KEY, roles=roles),
    )


def grant(consent, patient="pat-1", purposes=("clinical",), share_scope="consortium",
          at="2026-01-01T00:00:00Z"):
    return consent.grant(patient=patient, purposes=purposes,
                         share_scope=share_scope, at=at, by=patient)


def infer(env, request_id, patient="pat-1", institution="LH-XH", disease="DIS-GM",
          pattern="PT-FHRB", at="2026-02-01T00:00:00Z", **overrides):
    features = {
        "pattern_code": pattern,
        "symptoms": ["发热", "咽痛"],
        "tongue": "舌红苔薄黄",
        "pulse": "浮数",
        "age_band": "30-39",
        "sex": "女",
        "course_days": 2,
    }
    features.update(overrides.pop("features", {}))
    params = dict(request_id=request_id, institution_id=institution, doctor_id="doc-1",
                  patient_pseudonym=patient, disease_code=disease,
                  features=features, at=at)
    params.update(overrides)
    return env.inference.infer(**params)
