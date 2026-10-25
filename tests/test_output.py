import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
spec = importlib.util.spec_from_file_location("prediction_runner_test", ROOT / "code" / "main.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class OutputTests(unittest.TestCase):
    def test_rejects_duplicate_case(self):
        requests = [{"case_id": "case_1"}]
        rows = [{"case_id": "case_1", "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"}] * 2
        with self.assertRaises(ValueError):
            runner.validate_predictions(requests, rows)

    def test_rejects_unordered_step_numbers(self):
        requests = [{"case_id": "case_1"}]
        rows = [{"case_id": "case_1", "deletion_status": "COMPLETE", "steps_json": '[{"step_no":2,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_1","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_1"}]'}]
        with self.assertRaises(ValueError):
            runner.validate_predictions(requests, rows)

    def test_rejects_complete_without_cited_verification(self):
        requests = [{"case_id": "case_1"}]
        rows = [{"case_id": "case_1", "deletion_status": "COMPLETE", "steps_json": "[]"}]
        with self.assertRaisesRegex(ValueError, "complete"):
            runner.validate_predictions(requests, rows)


if __name__ == "__main__":
    unittest.main()
