import unittest

from src.audit.minimize import validate_request, MinimizationError
from tests.test_domain import base_request


class MinimizeTest(unittest.TestCase):
    def test_clean_request_passes(self):
        validate_request(base_request())

    def test_reject_realworld_identifier(self):
        req = base_request(features={**base_request()["features"],
                                     "note": "患者手机号13800138000"})
        with self.assertRaises(MinimizationError):
            validate_request(req)

    def test_reject_id_card_in_field(self):
        req = base_request(**{"patient_pseudonym": "310101199003078888"})
        with self.assertRaises(MinimizationError):
            validate_request(req)

    def test_reject_contract_outer_field(self):
        req = base_request(**{"住址": "某路某号"})
        with self.assertRaises(MinimizationError):
            validate_request(req)

    def test_reject_freeform_feature_key(self):
        req = base_request(features={**base_request()["features"],
                                     "chief_complaint_text": "咳嗽一月余"})
        with self.assertRaises(MinimizationError):
            validate_request(req)

    def test_reject_bad_age_band(self):
        req = base_request(features={**base_request()["features"], "age_band": "30"})
        with self.assertRaises(MinimizationError):
            validate_request(req)


if __name__ == "__main__":
    unittest.main()
