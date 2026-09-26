import unittest

from helpers import grant, infer, make_system
from src.patient_view import patient_view


class PatientViewTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent)
        self.rec = infer(self.env, "req-1")

    def view(self):
        return patient_view(
            decision=self.rec.to_dict(),
            disposition=self.env.disposition.disposition_for(self.rec.decision_id),
            terminology=self.env.terminology,
            registry=self.env.registry,
        )

    def test_pending_shows_nothing(self):
        view = self.view()
        self.assertEqual(view["status"], "pending")
        self.assertEqual(set(view), {"status", "message"})

    def test_confirmed_shows_doctor_content_only(self):
        self.env.disposition.confirm(decision_id=self.rec.decision_id,
                                     doctor_id="doc-1",
                                     at="2026-02-01T01:00:00Z", note="饭后温服")
        view = self.view()
        self.assertEqual(view["status"], "confirmed")
        self.assertEqual(view["formula"], "银翘散")
        self.assertEqual(view["disease"], "感冒")
        self.assertEqual(view["institution"], "龙华医院徐汇院区")
        self.assertEqual(view["doctor_note"], "饭后温服")
        for hidden in ("model_version", "model_output", "experience",
                       "request_id", "patient_pseudonym"):
            self.assertNotIn(hidden, view)

    def test_rejected_shows_no_recommendation(self):
        self.env.disposition.reject(decision_id=self.rec.decision_id,
                                    doctor_id="doc-1",
                                    at="2026-02-01T01:00:00Z", reason="不适用")
        view = self.view()
        self.assertEqual(view["status"], "none")
        self.assertNotIn("formula", view)

    def test_revised_shows_doctor_formula(self):
        self.env.disposition.revise(decision_id=self.rec.decision_id,
                                    doctor_id="doc-1",
                                    at="2026-02-01T01:00:00Z",
                                    new_formula_code="FM-GZT", note="改从桂枝汤")
        view = self.view()
        self.assertEqual(view["status"], "confirmed")
        self.assertEqual(view["formula"], "桂枝汤")


if __name__ == "__main__":
    unittest.main()
