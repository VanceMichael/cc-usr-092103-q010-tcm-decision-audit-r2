import unittest

from src.audit.repository import Repository
from src.audit.organizations import Organizations
from src.audit.terminology import Terminology
from src.audit.roles import Roles


def base_request(**overrides) -> dict:
    req = {
        "request_uid": "REQ-TEST-0001",
        "occurred_at": "2026-09-15T09:12:00+08:00",
        "org_id": "ORG-LH-XJH",
        "clinic_role": "ROLE-ATTENDING",
        "clinician_pseudonym": "D-AA01",
        "patient_pseudonym": "P-7F3A21",
        "consent_id": "CON-2026-0002",
        "disease_code": "DIS-COPD",
        "features": {
            "syndrome_code": "SYN-FHQF",
            "manifestation_codes": ["M-SF", "M-ND", "M-OB", "M-LB"],
            "tongue_code": "T-DH",
            "pulse_code": "P-XR",
            "age_band": "60-74",
            "sex": "F",
            "prior_formula_codes": ["FM-SJZT"],
        },
        "offline": False,
        "consultation_id": None,
    }
    req.update(overrides)
    return req


class TerminologyTest(unittest.TestCase):
    def setUp(self):
        self.terms = Terminology(Repository())

    def test_codes_resolve(self):
        self.assertEqual(self.terms.check_request("DIS-COPD", "SYN-FHQF", ["M-SF"]), [])

    def test_syndrome_disease_mismatch_flagged(self):
        unknown = self.terms.check_request("DIS-COPD", "SYN-FYXR", ["M-DK"])
        self.assertIn("SYN-FYXR!:DIS-COPD", unknown)

    def test_experience_scoped_to_disease_syndrome(self):
        hits = self.terms.experience_for(
            ["EXP-SCH-001", "EXP-LZH-002"], "DIS-LC", "SYN-FYXR")
        self.assertEqual([h["code"] for h in hits], ["EXP-LZH-002"])


class OrganizationsTest(unittest.TestCase):
    def setUp(self):
        repo = Repository()
        self.orgs = Organizations(repo)
        self.registrations = repo.registrations

    def test_campus_chain_to_headquarters(self):
        self.assertEqual(
            self.orgs.chain("ORG-LH-XJH"), ["ORG-LH-XJH", "ORG-LONGHUA"])

    def test_regional_site_inherits_center_registration(self):
        reg = self.orgs.resolve_registration(
            "ORG-PDTCM-CY", "model-fz-assist", self.registrations)
        self.assertEqual(reg["org_id"], "ORG-PDTCM")
        self.assertEqual(reg["allowed_versions"], ["1.4.0"])

    def test_alliance_detection(self):
        self.assertTrue(self.orgs.are_in_alliance("ORG-XHCH", "ORG-XHALL"))
        self.assertFalse(self.orgs.are_in_alliance("ORG-XHCH", "ORG-LH-XJH"))


class RolesTest(unittest.TestCase):
    def setUp(self):
        self.roles = Roles(Repository())

    def test_resident_can_invoke_but_not_confirm(self):
        self.assertTrue(self.roles.can("ROLE-RESIDENT", "PERM-CLINIC-INVOKE"))
        self.assertFalse(self.roles.can("ROLE-RESIDENT", "PERM-CLINIC-CONFIRM"))

    def test_registration_separation_of_duty(self):
        self.assertTrue(self.roles.can("ROLE-MODEL-ADMIN", "PERM-REG-VERSION"))
        self.assertFalse(self.roles.can("ROLE-MODEL-ADMIN", "PERM-REG-APPROVE"))
        self.assertTrue(self.roles.can("ROLE-MEDICAL-ADMIN", "PERM-REG-APPROVE"))

    def test_unknown_role(self):
        with self.assertRaises(KeyError):
            self.roles.permissions_of("ROLE-NOBODY")


if __name__ == "__main__":
    unittest.main()
