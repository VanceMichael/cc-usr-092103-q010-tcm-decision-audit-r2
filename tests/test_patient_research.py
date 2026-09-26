import unittest

from src.audit import AuditEngine, Repository
from src.audit.patient import PatientPortal, PatientDenied
from src.audit.research import ResearchExport, ResearchDenied, RESEARCH_SALT
from src.audit.serde import pseudonym
from tests.test_domain import base_request

ATTENDING = {"role": "ROLE-ATTENDING", "pseudonym": "D-AA01"}
RESEARCHER = {"role": "ROLE-RESEARCHER", "pseudonym": "DR-R-02"}


def _submit(engine, uid, **kw):
    return engine.submit(base_request(request_uid=uid, **kw))


class PatientPortalTest(unittest.TestCase):
    def setUp(self):
        self.engine = AuditEngine(Repository())

    def test_only_confirmed_visible(self):
        _submit(self.engine, "REQ-PV-1")                       # 待确认
        _submit(self.engine, "REQ-PV-2")
        self.engine.confirm("REQ-PV-2", ATTENDING, note="已确认")
        _submit(self.engine, "REQ-PV-3")
        self.engine.dismiss("REQ-PV-3", ATTENDING, reason="不采纳")

        portal = PatientPortal(self.engine)
        view = portal.view("P-7F3A21", {"role": "ROLE-PATIENT", "pseudonym": "P-7F3A21"})
        self.assertEqual(len(view), 1)
        self.assertNotIn("model", view[0])
        self.assertTrue(view[0]["confirmed_by_doctor"])
        self.assertIn("玉屏风散", "".join(view[0]["doctor_final_formulas"]))

    def test_revised_shows_doctor_formula_not_model(self):
        _submit(self.engine, "REQ-PV-4")
        self.engine.revise("REQ-PV-4", ATTENDING,
                           final_formula_codes=["FM-SJZT"], note="改用四君子汤")
        portal = PatientPortal(self.engine)
        view = portal.view("P-7F3A21", {"role": "ROLE-PATIENT", "pseudonym": "P-7F3A21"})
        formulas = "".join(view[0]["doctor_final_formulas"])
        self.assertIn("四君子汤", formulas)
        self.assertNotIn("玉屏风散", formulas)

    def test_other_patient_denied(self):
        portal = PatientPortal(self.engine)
        with self.assertRaises(PatientDenied):
            portal.view("P-7F3A21", {"role": "ROLE-PATIENT", "pseudonym": "P-9C10B4"})

    def test_non_patient_role_denied(self):
        portal = PatientPortal(self.engine)
        with self.assertRaises(PatientDenied):
            portal.view("P-7F3A21", {"role": "ROLE-AUDITOR", "pseudonym": "P-7F3A21"})


class ResearchExportTest(unittest.TestCase):
    def setUp(self):
        self.engine = AuditEngine(Repository())
        # 三次含科研授权（CON-2026-0002）的复诊，全部确认
        for i in range(3):
            _submit(self.engine, f"REQ-RS-{i}", occurred_at=f"2026-09-{(5+i*7):02d}T11:00:00+08:00")
            self.engine.confirm(f"REQ-RS-{i}", ATTENDING, note="复诊")

    def test_export_uses_separate_pseudonym(self):
        export = ResearchExport(self.engine).export_dataset(
            dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052", actor=RESEARCHER, k=2)
        self.assertEqual(export["included"], 3)
        expected = pseudonym("P-7F3A21", RESEARCH_SALT, prefix="R")
        self.assertTrue(all(r["research_pseudonym"] == expected for r in export["rows"]))
        # 导出中不出现临床化名、医生化名、机构、完整时间戳
        raw = str(export["rows"])
        self.assertNotIn("P-7F3A21", raw)
        self.assertNotIn("D-AA01", raw)
        self.assertNotIn("ORG-", raw)

    def test_export_without_research_consent_excluded(self):
        # CON-2026-0001 仅临床用途的病例不进入科研导出
        req = base_request(request_uid="REQ-RS-NC", consent_id="CON-2026-0001")
        self.engine.submit(req)
        self.engine.confirm("REQ-RS-NC", ATTENDING, note="临床")
        export = ResearchExport(self.engine).export_dataset(
            dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052", actor=RESEARCHER, k=2)
        self.assertEqual(export["included"], 3)
        self.assertTrue(any("科研用途" in s["reason"] for s in export["skipped"]))

    def test_wrong_ethics_or_dataset_denied(self):
        research = ResearchExport(self.engine)
        with self.assertRaises(ResearchDenied):
            research.export_dataset(dataset="DS-COPD-OUTCOME",
                                    ethics_ref="伦理1999-000", actor=RESEARCHER)
        with self.assertRaises(ResearchDenied):
            research.export_dataset(dataset="DS-LC-UNAPPROVED",
                                    ethics_ref="伦理2026-052", actor=RESEARCHER)

    def test_withdrawn_consent_excluded(self):
        self.engine.withdraw_consent(
            "CON-2026-0002", {"role": "ROLE-CONSULT-DESK", "pseudonym": "DESK"},
            at="2026-09-20T09:00:00+08:00", reason="撤回科研")
        export = ResearchExport(self.engine).export_dataset(
            dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052", actor=RESEARCHER, k=2)
        self.assertEqual(export["included"], 0)
        self.assertTrue(all("撤回" in s["reason"] for s in export["skipped"]))

    def test_export_attempt_is_itself_an_event(self):
        ResearchExport(self.engine).export_dataset(
            dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052", actor=RESEARCHER, k=2)
        ResearchExport(self.engine).deny_export(
            dataset="DS-BAD", ethics_ref="伦理X", actor=RESEARCHER,
            reason="超出范围", at="2026-09-21T09:00:00+08:00")
        self.assertEqual(len(self.engine.events.of_type("RESEARCH_EXPORT")), 1)
        self.assertEqual(len(self.engine.events.of_type("RESEARCH_EXPORT_DENIED")), 1)

    def test_k_anonymity_suppression(self):
        export = ResearchExport(self.engine).export_dataset(
            dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052", actor=RESEARCHER, k=5)
        self.assertEqual(export["included"], 3)
        self.assertTrue(all(r["age_band"] == "*" and r["sex"] == "*" for r in export["rows"]))


if __name__ == "__main__":
    unittest.main()
