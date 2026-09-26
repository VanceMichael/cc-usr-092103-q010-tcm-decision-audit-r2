import json
import unittest

from helpers import FIXTURES
from src.errors import DirectIdentifierError, ValidationError
from src.minimization import FEATURE_ALLOWLIST, minimize_features


class MinimizationTest(unittest.TestCase):
    def test_strips_non_allowlisted_fields(self):
        minimized = minimize_features({
            "pattern_code": "PT-FHRB",
            "age": 37,            # 精确年龄不在白名单
            "nickname": "小李",
            "gps": "31.19,121.43",
        })
        self.assertEqual(minimized, {"pattern_code": "PT-FHRB"})

    def test_direct_identifiers_rejected(self):
        for field in ("name", "id_number", "phone", "medical_record_no"):
            with self.assertRaises(DirectIdentifierError):
                minimize_features({"pattern_code": "PT-FHRB", field: "x"})

    def test_required_feature_missing(self):
        with self.assertRaises(ValidationError):
            minimize_features({"tongue": "舌红"})

    def test_output_only_allowlisted(self):
        minimized = minimize_features({
            "pattern_code": "PT-FHRB", "symptoms": ["发热"], "tongue": "舌红",
            "pulse": "数", "age_band": "30-39", "sex": "女", "course_days": 2,
            "extra_field": "应被丢弃",
        })
        self.assertTrue(set(minimized) <= FEATURE_ALLOWLIST)
        self.assertNotIn("extra_field", minimized)

    def test_fixture_sample_request_minimizes(self):
        req = json.loads((FIXTURES / "inference_request.json").read_text(encoding="utf-8"))
        minimized = minimize_features(req["features"])
        self.assertTrue(set(minimized) <= FEATURE_ALLOWLIST)
        self.assertEqual(minimized["pattern_code"], "PT-FHRB")


if __name__ == "__main__":
    unittest.main()
