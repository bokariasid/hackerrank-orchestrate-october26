import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from evidence import load_evidence, parse_image, parse_json, parse_sql
from identity import resolve_identity


ROOT = Path(__file__).resolve().parents[1] / "dataset"


class EvidenceTests(unittest.TestCase):
    def test_failover_replica_preserves_remaining_account(self):
        observations = load_evidence(ROOT)
        account = [o for o in observations if o["resource_id"] == "account_005" and o["evidence_id"] == "evidence_54"]
        self.assertTrue(any(o["state"] == "PRESENT" and o["store"] == "customer_profiles_failover_replica" for o in account))
        scopes = [o for o in observations if o["state"] == "SCOPE" and o["evidence_id"] == "evidence_54"]
        self.assertIn("customer_profiles_primary", {o["data"]["covered_store"] for o in scopes})
        self.assertIn("customer_profiles_failover_replica", {o["data"]["covered_store"] for o in scopes})

    def test_explicit_readback_absence(self):
        observations = load_evidence(ROOT)
        self.assertTrue(any(o["resource_id"] == "account_2064" and o["state"] == "ABSENT" and o["evidence_id"] == "evidence_94" for o in observations))

    def test_structured_log_readback_absence(self):
        observations = load_evidence(ROOT)
        self.assertTrue(any(o["resource_id"] == "account_3960" and o["state"] == "ABSENT" and o["evidence_id"] == "evidence_06" for o in observations))

    def test_syslog_trace_joins_lookup_key_to_readback(self):
        observations = load_evidence(ROOT)
        self.assertTrue(any(o["resource_id"] == "account_8316" and o["state"] == "ABSENT"
                            and o["data"].get("lookup_key") == "mrios950798@fastmail.com" for o in observations))

    def test_bare_syslog_trace_and_pipe_log(self):
        observations = load_evidence(ROOT)
        self.assertTrue(any(o["resource_id"] == "account_3075" and o["state"] == "ABSENT"
                            and str(o["data"].get("lookup_key", "")).casefold() == "keiko.sato@outlook.com" for o in observations))
        self.assertTrue(any(o["resource_id"] == "account_1334" and o["state"] == "PRESENT"
                            and o["data"].get("correlation_key") == "m.moreau38@yahoo.com" for o in observations))

    def test_historical_email_matches_current_subject(self):
        import csv
        with (ROOT / "user_identities.csv").open() as file:
            identities = list(csv.DictReader(file))
        with (ROOT / "user_audit_log.csv").open() as file:
            changes = list(csv.DictReader(file))
        old = next(x for x in changes if x["field_name"] == "email" and x["old_value"] and x["old_value"] != x["new_value"])
        subject = resolve_identity({"email": old["old_value"], "audit_at": "2026-12-31T00:00:00Z"}, identities, changes)
        self.assertEqual(subject["user_id"], old["user_id"])
        self.assertIn(old["new_value"].casefold(), subject["values"])

    def test_image_model_output_is_resource_scoped(self):
        class FakeModel:
            def extract(self, prompt, required_keys, image_path=None):
                return {"records": [{"resource_id": "search_42", "system_id": "search_service", "state": "PRESENT", "email": "old@example.test"},
                                    {"resource_id": "invented", "state": "PRESENT"}]}

        rows = parse_image(ROOT / "evidence" / "images" / "unused.png", "evidence_1", "2026-01-01T00:00:00Z", FakeModel())
        self.assertEqual([row["resource_id"] for row in rows], ["search_42"])

    def test_catalog_link_connects_pdf_document_reference(self):
        observations = load_evidence(ROOT)
        self.assertTrue(any(o["resource_id"] == "export_1842" and o["evidence_id"] == "evidence_18" for o in observations))
        self.assertFalse(any(o["resource_id"] == "tombstone_17" and o["evidence_id"] == "evidence_27" for o in observations))

    def test_empty_snapshots_emit_scoped_absence_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sql = root / "snapshot.sql"
            sql.write_text("CREATE TABLE snapshot_scope (origin_system TEXT, captured_at TEXT); INSERT INTO snapshot_scope VALUES ('account_service', '2026-02-19T00:00:00Z'); CREATE TABLE customer_profiles (resource_id TEXT, email TEXT);")
            json_file = root / "snapshot.json"
            json_file.write_text('{"snapshots":[{"collection":"customer_profiles","data_store":"account_service","captured_at":"2026-02-19T00:00:00Z","records":[]}]}')
            for rows in (parse_sql(sql, "evidence_sql", "2026-02-20T00:00:00Z"),
                         parse_json(json_file, "evidence_json", "2026-02-20T00:00:00Z")):
                scope = next(o for o in rows if o["state"] == "SCOPE")
                self.assertEqual(scope["data"]["resource_prefix"], "account_")
                self.assertEqual(scope["data"]["covered_system"], "account_service")
                self.assertEqual(scope["data"]["covered_store"], "account_service")
                self.assertEqual(scope["time"], "2026-02-19T00:00:00Z")
                self.assertEqual(scope["available_at"], "2026-02-20T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
