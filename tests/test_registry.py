import unittest

from helpers import make_system
from src.errors import RoleError
from src.events import EventLog
from src.registry import InstitutionRegistry
from helpers import FIXTURES


def adverse_events(log, institution, version, count, at):
    for i in range(count):
        log.append(event_id=f"adv-{i}", type="ADVERSE_OUTCOME_REPORTED",
                   occurred_at=at, recorded_at=at, actor="doc-1",
                   payload={"decision_id": f"dec-{i}", "institution_id": institution,
                            "model_version": version, "description": "皮疹",
                            "severity": "轻度"})


class RegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = InstitutionRegistry.load(FIXTURES / "institutions.json")

    def test_hierarchy_loaded(self):
        self.assertEqual(self.registry.institution("LH-XH").parent, "LH")
        self.assertEqual(self.registry.institution("CHS-CF").tier, "医联体")
        self.assertEqual(self.registry.institution("RMC-JA").tier, "区域医疗中心")
        children = {i.institution_id for i in self.registry.children("LH")}
        self.assertEqual(children, {"LH-XH", "LH-PD"})

    def test_permission_granted(self):
        snap = self.registry.permission_snapshot("LH-XH", "fz-1.0", "DIS-GM",
                                                 "2026-01-01T00:00:00Z")
        self.assertTrue(snap["permitted"])
        self.assertEqual(snap["reasons"], [])

    def test_disease_out_of_scope(self):
        snap = self.registry.permission_snapshot("LH-PD", "fz-1.0", "DIS-ZF",
                                                 "2026-01-01T00:00:00Z")
        self.assertFalse(snap["permitted"])
        self.assertIn("病种不在适用范围", snap["reasons"])

    def test_version_expired(self):
        snap = self.registry.permission_snapshot("LH-PD", "fz-1.0", "DIS-GM",
                                                 "2026-05-02T00:00:00Z")
        self.assertFalse(snap["permitted"])
        self.assertIn("已过停用日期", snap["reasons"])

    def test_version_not_registered(self):
        snap = self.registry.permission_snapshot("RMC-JA", "fz-2.0", "DIS-GM",
                                                 "2026-04-01T00:00:00Z")
        self.assertFalse(snap["permitted"])
        self.assertIn("机构未登记该模型版本", snap["reasons"])

    def test_adverse_threshold_deactivates(self):
        log = EventLog()
        adverse_events(log, "LH-XH", "fz-2.0", 3, "2026-04-01T00:00:00Z")
        snap = self.registry.permission_snapshot("LH-XH", "fz-2.0", "DIS-GM",
                                                 "2026-04-02T00:00:00Z", log.events)
        self.assertFalse(snap["permitted"])
        self.assertIn("不良事件达到停用阈值", snap["reasons"])
        # 上报发生前的时刻仍允许
        before = self.registry.permission_snapshot("LH-XH", "fz-2.0", "DIS-GM",
                                                   "2026-03-31T00:00:00Z", log.events)
        self.assertTrue(before["permitted"])

    def test_registration_and_deactivation_events(self):
        env = make_system()
        env.admin.register_version(
            institution_id="RMC-JA",
            registration={"model_version": "fz-2.0", "diseases": ["DIS-GM"],
                          "effective_from": "2026-02-01T00:00:00Z"},
            at="2026-01-20T00:00:00Z", by="ops-1")
        ok = env.registry.permission_snapshot("RMC-JA", "fz-2.0", "DIS-GM",
                                              "2026-02-15T00:00:00Z", env.log.events)
        self.assertTrue(ok["permitted"])
        env.admin.deactivate_version(institution_id="RMC-JA", model_version="fz-2.0",
                                     reason="质控复核", at="2026-03-01T00:00:00Z", by="ops-1")
        stopped = env.registry.permission_snapshot("RMC-JA", "fz-2.0", "DIS-GM",
                                                   "2026-03-02T00:00:00Z", env.log.events)
        self.assertFalse(stopped["permitted"])
        self.assertIn("版本已被机构停用", stopped["reasons"])
        # 停用事件不影响其发生之前的"当时"
        still_ok = env.registry.permission_snapshot("RMC-JA", "fz-2.0", "DIS-GM",
                                                    "2026-02-15T00:00:00Z", env.log.events)
        self.assertTrue(still_ok["permitted"])

    def test_registry_manage_requires_role(self):
        env = make_system()
        with self.assertRaises(RoleError):
            env.admin.deactivate_version(institution_id="LH-XH", model_version="fz-1.0",
                                         reason="x", at="2026-03-01T00:00:00Z",
                                         by="doc-1", by_role="researcher")


if __name__ == "__main__":
    unittest.main()
