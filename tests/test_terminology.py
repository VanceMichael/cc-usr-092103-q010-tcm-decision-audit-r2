import unittest

from helpers import FIXTURES
from src.errors import NotFoundError
from src.terminology import Terminology


class TerminologyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terminology = Terminology.load(FIXTURES / "terminology.json")

    def test_lookup_by_code(self):
        self.assertEqual(self.terminology.pattern("PT-FHRB").name, "风热犯表证")
        self.assertEqual(self.terminology.formula("FM-YQS").name, "银翘散")
        self.assertEqual(self.terminology.disease("DIS-GM").name, "感冒")

    def test_experience_versioned(self):
        latest = self.terminology.experience("EXP-GU")
        self.assertEqual(latest.version, 2)
        v1 = self.terminology.experience("EXP-GU", 1)
        self.assertIn("银翘散", v1.summary)

    def test_unknown_code_raises(self):
        with self.assertRaises(NotFoundError):
            self.terminology.pattern("PT-XXX")
        with self.assertRaises(NotFoundError):
            self.terminology.experience("EXP-GU", 99)


if __name__ == "__main__":
    unittest.main()
