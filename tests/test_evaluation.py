import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code" / "evaluation"))

from main import metrics, unavailable_expected_steps, write_reports


class EvaluationTests(unittest.TestCase):
    def test_metrics_distinguish_status_from_order(self):
        a = {"phase": "VERIFICATION", "operation": "VERIFY_RECORD_ABSENT", "system_id": "account_service",
             "resource_id": "account_1", "state": "ABSENT", "evidence_id": "evidence_1", "step_no": 1}
        b = dict(a, resource_id="account_2", step_no=2)
        expected = [{"case_id": "x", "deletion_status": "COMPLETE", "steps_json": __import__("json").dumps([a, b])}]
        actual = [{"case_id": "x", "deletion_status": "COMPLETE", "steps_json": __import__("json").dumps([dict(b, step_no=1), dict(a, step_no=2)])}]
        result = metrics(expected, actual)
        self.assertEqual(result["status_accuracy"], 1.0)
        self.assertEqual(result["step_recall"], 1.0)
        self.assertEqual(result["sequence_accuracy"], 0.0)
        self.assertEqual(result["status_mismatch_cases"], [])

    def test_report_directory_is_selected_by_caller(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "code" / "evaluation"
            score = {"status_accuracy": 1.0, "step_precision": 1.0, "step_recall": 1.0,
                     "field_accuracy": 1.0, "sequence_accuracy": 1.0}
            usage = {"provider": "none", "model": "none", "calls": 0, "input_tokens": 0,
                     "output_tokens": 0, "requests": 1, "cache_hits": 0, "elapsed_seconds": 0,
                     "wall_seconds": 0, "screenshots_processed": False}
            write_reports(destination, score, usage, [{}])
            self.assertTrue((destination / "evaluation_report.md").exists())
            self.assertTrue((destination / "usage_report.md").exists())

    def test_evaluation_lists_expected_resources_unavailable_at_audit_time(self):
        expected = [{"case_id": "case_1", "steps_json": __import__("json").dumps([
            {"operation": "VERIFY_RECORD_PRESENT", "resource_id": "audit_record_1"},
            {"operation": "VERIFY_RECORD_ABSENT", "resource_id": "account_2"},
        ])}]
        requests = [{"case_id": "case_1", "audit_at": "2026-02-10T00:00:00Z"}]
        observations = [{"resource_id": "audit_record_1", "available_at": "2026-02-11T00:00:00Z"}]
        result = unavailable_expected_steps(expected, requests, observations, [])
        self.assertEqual(result, [
            {"case_id": "case_1", "resource_id": "audit_record_1", "operation": "VERIFY_RECORD_PRESENT", "availability": "after_audit"},
            {"case_id": "case_1", "resource_id": "account_2", "operation": "VERIFY_RECORD_ABSENT", "availability": "not_found"},
        ])


if __name__ == "__main__":
    unittest.main()
