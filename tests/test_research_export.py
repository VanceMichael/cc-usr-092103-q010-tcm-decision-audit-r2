import unittest

from helpers import grant, infer, make_system
from src.errors import RoleError
from src.research_export import FORBIDDEN_FIELDS


class ResearchExportTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent, patient="pat-r", purposes=("clinical", "research"))
        grant(self.env.consent, patient="pat-c", purposes=("clinical",))
        grant(self.env.consent, patient="pat-w", purposes=("clinical", "research"))
        self.env.consent.withdraw(patient="pat-w", purposes=("research",),
                                  at="2026-03-01T00:00:00Z", by="pat-w")
        self.rec_r = infer(self.env, "req-r", patient="pat-r")
        infer(self.env, "req-c", patient="pat-c")
        infer(self.env, "req-w", patient="pat-w")
        self.env.disposition.confirm(decision_id=self.rec_r.decision_id,
                                     doctor_id="doc-1",
                                     at="2026-02-01T01:00:00Z", note="照方")

    def test_export_filters_by_purpose(self):
        rows = self.env.exporter.export(at="2026-04-01T00:00:00Z")
        self.assertEqual(len(rows), 1)  # 仅 pat-r：pat-c 无科研授权，pat-w 已撤回

    def test_withdrawal_excludes_retroactively(self):
        before = self.env.exporter.export(at="2026-02-15T00:00:00Z")
        self.assertEqual(len(before), 2)  # 撤回前 pat-r 与 pat-w
        after = self.env.exporter.export(at="2026-04-01T00:00:00Z")
        self.assertEqual(len(after), 1)

    def test_pseudonym_is_irreversible_and_stable(self):
        rows = self.env.exporter.export(at="2026-04-01T00:00:00Z")
        key = rows[0]["patient_key"]
        self.assertNotEqual(key, "pat-r")
        self.assertEqual(len(key), 64)
        again = self.env.exporter.export(at="2026-04-02T00:00:00Z")
        self.assertEqual(again[0]["patient_key"], key)

    def test_rows_carry_no_identifiers(self):
        rows = self.env.exporter.export(at="2026-04-01T00:00:00Z")
        for row in rows:
            self.assertTrue(set(row).isdisjoint(FORBIDDEN_FIELDS))
            self.assertEqual(row["occurred_month"], "2026-02")
            self.assertEqual(row["institution_tier"], "院区")
            self.assertEqual(row["age_band"], "30-39")

    def test_final_formula_comes_from_doctor(self):
        rows = self.env.exporter.export(at="2026-04-01T00:00:00Z")
        self.assertTrue(rows[0]["doctor_confirmed"])
        self.assertEqual(rows[0]["final_formula_code"], "FM-YQS")

    def test_export_requires_role(self):
        with self.assertRaises(RoleError):
            self.env.exporter.export(at="2026-04-01T00:00:00Z", by_role="auditor")


if __name__ == "__main__":
    unittest.main()
