import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from graph import predict


SUBJECT = {"user_id": "user_1", "email": "old@example.test", "name": "A Name", "values": {"user_1", "old@example.test"}}
REQUEST = {"case_id": "case_x", "email": "old@example.test", "reported_at": "2026-02-01T00:00:00Z", "audit_at": "2026-03-01T00:00:00Z", "request_text": "Please delete my account and personal data."}


def obs(resource, time, state, data, evidence="evidence_1", store="primary"):
    return {"resource_id": resource, "system_id": "", "time": time, "state": state, "data": data, "evidence_id": evidence, "store": store}


class GraphTests(unittest.TestCase):
    def test_nondeletion_request_is_not_applicable(self):
        request = dict(REQUEST, request_text="Please correct my email address. Do not delete my account.")
        self.assertEqual(predict(request, SUBJECT, [], [], [])[0:2], ("NOT_APPLICABLE", []))

    def test_negated_closure_and_marketing_only_are_not_deletion(self):
        for text in ("I need help resetting the login. Do not close the account.",
                     "Please remove me from marketing lists only. I still want receipts."):
            with self.subTest(text=text):
                request = dict(REQUEST, request_text=text)
                self.assertEqual(predict(request, SUBJECT, [], [], [])[0], "NOT_APPLICABLE")

    def test_korean_deletion_request(self):
        request = dict(REQUEST, request_text="이 계정의 영구 삭제를 요청합니다.")
        self.assertEqual(predict(request, SUBJECT, [], [], [])[0], "INCOMPLETE")

    def test_later_observation_cannot_override_audit_state(self):
        records = [obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"}),
                   obs("account_7", "2026-03-02T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"})]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["operation"], "REPORT_REMAINING_RECORD")
        self.assertEqual(steps[0]["evidence_id"], "evidence_1")

    def test_later_file_capture_cannot_backdate_nested_record(self):
        late = obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"})
        late["available_at"] = "2026-03-02T00:00:00Z"
        status, steps = predict(REQUEST, SUBJECT, [late], [], [])
        self.assertEqual((status, steps), ("INCOMPLETE", []))

    def test_late_activity_capture_cannot_backdate_subject_resource_link(self):
        activity = [{"event_time": "2026-02-20T00:00:00Z", "available_at": "2026-03-02T00:00:00Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "subject_ref=user_1"}]
        self.assertEqual(predict(REQUEST, SUBJECT, [], activity, []), ("INCOMPLETE", []))

    def test_user_id_substring_inside_another_email_does_not_link_resource(self):
        activity = [{"event_time": "2026-02-20T00:00:00Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "contact=user_1.other@example.test"}]
        self.assertEqual(predict(REQUEST, SUBJECT, [], activity, []), ("INCOMPLETE", []))

    def test_no_evidence_is_not_complete(self):
        self.assertEqual(predict(REQUEST, SUBJECT, [], [], []), ("INCOMPLETE", []))

    def test_scoped_snapshot_proves_absence_of_linked_account_only(self):
        scope = obs("", "2026-02-20T00:00:00Z", "SCOPE", {"resource_prefix": "account_"}, "evidence_scope", "customer_profiles_primary")
        activity = [{"event_time": "2026-02-01T00:00:00Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "customer_email=old@example.test"}]
        status, steps = predict(REQUEST, SUBJECT, [scope], activity, [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in steps],
                         [("VERIFY_RECORD_ABSENT", "account_7", "evidence_scope")])

    def test_scope_does_not_prove_absence_for_ambiguous_time_hint(self):
        request = dict(REQUEST, reported_at="2026-02-20T12:00:00Z")
        scope = obs("", "2026-02-21T00:00:00Z", "SCOPE", {"resource_prefix": "account_"}, "evidence_scope", "customer_profiles_primary")
        activity = [
            {"event_time": "2026-02-20T11:59:50Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "Worker completed operation."},
            {"event_time": "2026-02-20T11:58:50Z", "event_type": "DELETE_RECORD", "resource_id": "account_8", "details": "Worker completed operation."},
        ]
        _, steps = predict(request, SUBJECT, [scope], activity, [])
        self.assertFalse(any(s["operation"] == "VERIFY_RECORD_ABSENT" and s["evidence_id"] == "evidence_scope" for s in steps))

    def test_explicit_resource_reference_links_identifier_free_record(self):
        records = [
            obs("search_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test", "source_resource_id": "account_7"}),
            obs("account_7", "2026-02-20T00:00:00Z", "ABSENT", {"resource_id": "account_7"}, "evidence_2"),
        ]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertIn(("VERIFY_RECORD_ABSENT", "account_7"),
                      {(s["operation"], s["resource_id"]) for s in steps})

    def test_nearby_anonymization_and_readback_do_not_link_without_subject_edge(self):
        request = dict(REQUEST, reported_at="2026-02-20T12:00:00Z", request_text="Delete my account, but keep anonymous analytics statistics.")
        record = obs("analytics_7", "2026-02-21T00:00:00Z", "PRESENT", {"email": None, "phone": None, "email_hash": None, "customer_reference": None})
        activity = [{"event_time": "2026-02-20T11:54:00Z", "event_type": "ANONYMIZE_RECORD", "resource_id": "analytics_7", "details": "Policy action completed."}]
        status, steps = predict(request, SUBJECT, [record], activity, [])
        self.assertEqual((status, steps), ("INCOMPLETE", []))

    def test_subject_linked_activity_with_identifier_free_analytics_produces_proof(self):
        request = dict(REQUEST, request_text="Delete my account.")
        record = obs("analytics_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": None, "customer_reference": None})
        activity = [{"event_time": "2026-02-20T00:00:00Z", "event_type": "ANONYMIZE_RECORD", "resource_id": "analytics_7", "details": "subject_ref=user_1"}]
        status, steps = predict(request, SUBJECT, [record], activity, [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([(s["operation"], s["evidence_id"]) for s in steps], [("VERIFY_RECORD_ANONYMIZED", "evidence_1")])

    def test_ambiguous_recent_analytics_operations_do_not_link(self):
        request = dict(REQUEST, reported_at="2026-02-20T12:00:00Z", request_text="Delete my account, but keep anonymous analytics statistics.")
        records = [obs("analytics_7", "2026-02-21T00:00:00Z", "PRESENT", {"email": None, "customer_reference": None}),
                   obs("analytics_8", "2026-02-21T00:00:00Z", "PRESENT", {"email": None, "customer_reference": None})]
        activity = [{"event_time": "2026-02-20T11:54:00Z", "event_type": "ANONYMIZE_RECORD", "resource_id": "analytics_7", "details": "Policy action completed."},
                    {"event_time": "2026-02-20T11:55:00Z", "event_type": "ANONYMIZE_RECORD", "resource_id": "analytics_8", "details": "Policy action completed."}]
        status, steps = predict(request, SUBJECT, records, activity, [])
        self.assertEqual((status, steps), ("INCOMPLETE", []))

    def test_replica_presence_overrides_primary_absence(self):
        records = [obs("account_7", "2026-02-20T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}),
                   obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"}, "evidence_2", "failover")]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["evidence_id"], "evidence_2")

    def test_newer_primary_readback_overrides_old_primary_snapshot(self):
        records = [obs("account_7", "2026-02-10T00:00:00Z", "PRESENT", {"email": "old@example.test"}, store="customer_profiles"),
                   obs("account_7", "2026-02-20T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}, "evidence_2", "profile_lookups")]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(steps[0]["evidence_id"], "evidence_2")

    def test_active_flow_precedes_record_deletion(self):
        records = [obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"}),
                   obs("data_flow_7", "2026-02-20T00:00:00Z", "PRESENT", {"customer_email": "old@example.test", "enabled": True})]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertLess([x["operation"] for x in steps].index("DISABLE_DATA_FLOW"), [x["operation"] for x in steps].index("DELETE_RECORD"))
        self.assertEqual([x["step_no"] for x in steps], list(range(1, len(steps) + 1)))

    def test_clean_tombstone_preserves_stable_reference(self):
        records = [obs("tombstone_7", "2026-02-20T00:00:00Z", "PRESENT", {"customer_reference": "user_1", "email": None, "phone": None, "full_name": None, "email_hash": None})]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(steps[0]["operation"], "VERIFY_RECORD_REDACTED")

    def test_analytics_user_reference_requires_anonymization(self):
        records = [obs("analytics_7", "2026-02-20T00:00:00Z", "PRESENT", {"user_id": "user_1", "email": None, "phone": None})]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["operation"], "REPORT_EXPOSED_PII")

    def test_absent_retained_or_treated_record_is_not_reported_as_exposed(self):
        for resource, data, check in (
            ("audit_record_7", {"subject_reference": "user_1"}, "VERIFY_RECORD_PRESENT"),
            ("support_7", {"lookup_key": "old@example.test"}, "VERIFY_RECORD_REDACTED"),
            ("analytics_7", {"lookup_key": "old@example.test"}, "VERIFY_RECORD_ANONYMIZED"),
        ):
            with self.subTest(resource=resource):
                status, steps = predict(REQUEST, SUBJECT, [obs(resource, "2026-02-20T00:00:00Z", "ABSENT", data)], [], [])
                self.assertEqual(status, "INCOMPLETE")
                self.assertNotIn("REPORT_EXPOSED_PII", {step["operation"] for step in steps})
                self.assertIn(check, {step["operation"] for step in steps})

    def test_policy_is_read_from_supplied_rules(self):
        records = [obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"})]
        rules = [{"data_category": "USER_PROFILE", "required_action": "RETAIN", "condition": "Always"}]
        status, steps = predict(REQUEST, SUBJECT, records, [], rules)
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(steps[0]["operation"], "VERIFY_RECORD_PRESENT")

    def test_expired_security_retention_changes_required_action(self):
        rules = [{"data_category": "SECURITY_AUDIT_LOG", "required_action": "RETAIN", "condition": "Record is within the supplied retention period"}]
        record = obs("audit_record_7", "2026-02-20T00:00:00Z", "PRESENT", {"subject_reference": "user_1", "retention_until": "2026-02-28T00:00:00Z"})
        status, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in steps],
                         ["REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"])
        record["data"]["retention_until"] = "2026-04-01T00:00:00Z"
        status, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([s["operation"] for s in steps], ["VERIFY_RECORD_PRESENT"])

    def test_inactive_legal_hold_no_longer_requires_retention(self):
        rules = [{"data_category": "LEGAL_HOLD_RECORD", "required_action": "RETAIN", "condition": "A supplied legal hold is active"}]
        record = obs("legal_hold_7", "2026-02-20T00:00:00Z", "PRESENT", {"record_type": "LEGAL_HOLD_RECORD", "subject_reference": "user_1", "hold_active": False})
        status, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertEqual(status, "INCOMPLETE")
        self.assertIn("DELETE_RECORD", [s["operation"] for s in steps])
        record["data"]["hold_active"] = True
        status, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([s["operation"] for s in steps], ["VERIFY_RECORD_PRESENT"])

    def test_support_context_condition_preserves_shared_message(self):
        rules = [{"data_category": "SUPPORT_MESSAGE", "required_action": "REDACT", "condition": "Conversation contains shared customer, agent, or order context that must be retained"}]
        record = obs("support_7", "2026-02-20T00:00:00Z", "PRESENT", {"record_type": "SUPPORT_MESSAGE", "email": "old@example.test", "shared_context": False})
        _, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertIn("DELETE_RECORD", [s["operation"] for s in steps])
        record["data"]["shared_context"] = True
        _, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertIn("REDACT_RECORD", [s["operation"] for s in steps])

    def test_shared_attachment_is_redacted_instead_of_deleted(self):
        rules = [{"data_category": "SUPPORT_ATTACHMENT", "required_action": "DELETE", "condition": "Attachment belongs only to the deletion subject"}]
        record = obs("support_7", "2026-02-20T00:00:00Z", "PRESENT", {"record_type": "SUPPORT_ATTACHMENT", "email": "old@example.test", "subject_only": False})
        _, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertIn("REDACT_RECORD", [s["operation"] for s in steps])
        record["data"]["subject_only"] = True
        _, steps = predict(REQUEST, SUBJECT, [record], [], rules)
        self.assertIn("DELETE_RECORD", [s["operation"] for s in steps])

    def test_tombstone_requires_stable_reference(self):
        earlier = obs("tombstone_7", "2026-02-01T00:00:00Z", "PRESENT", {"email": "old@example.test"})
        current = obs("tombstone_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": None, "name": None, "phone": None, "email_hash": None}, "evidence_2")
        status, steps = predict(REQUEST, SUBJECT, [earlier, current], [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertIn("VERIFY_RECORD_REDACTED", [s["operation"] for s in steps])

    def test_incomplete_plan_checks_account_named_by_workflow_even_without_readback(self):
        records = [obs("search_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"})]
        activity = [{"event_time": "2026-01-31T23:59:50Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "subject_ref=user_1"}]
        status, steps = predict(REQUEST, SUBJECT, records, activity, [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertIn(("VERIFY_RECORD_ABSENT", "account_7"), {(step["operation"], step["resource_id"]) for step in steps})

    def test_incomplete_plan_checks_subject_linked_analytics_without_readback(self):
        activity = [{"event_time": "2026-02-20T00:00:00Z", "event_type": "ANONYMIZE_RECORD", "resource_id": "analytics_7", "details": "subject_ref=user_1"}]
        status, steps = predict(REQUEST, SUBJECT, [], activity, [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in steps],
                         [("VERIFY_RECORD_ANONYMIZED", "analytics_7", "")])

    def test_incomplete_plan_checks_subject_linked_flow_without_readback(self):
        activity = [{"event_time": "2026-02-20T00:00:00Z", "event_type": "CHECK_DATA_FLOW", "resource_id": "data_flow_7", "details": "subject_ref=user_1"}]
        status, steps = predict(REQUEST, SUBJECT, [], activity, [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in steps],
                         [("VERIFY_DATA_FLOW_DISABLED", "data_flow_7", "")])

    def test_catalog_identity_link_alone_does_not_claim_resource_state(self):
        activity = [{"event_time": "2026-02-20T00:00:00Z", "event_type": "LINK_RESOURCE_IDENTITY", "resource_id": "tombstone_7", "details": "subject_ref=user_1"}]
        self.assertEqual(predict(REQUEST, SUBJECT, [], activity, []), ("INCOMPLETE", []))

    def test_shared_report_time_does_not_assign_unlinked_account(self):
        records = [obs("search_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"})]
        activity = [{"event_time": "2026-01-31T23:59:50Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "Deletion submitted."}]
        request = dict(REQUEST, report_time_ambiguous=True)
        _, steps = predict(request, SUBJECT, records, activity, [])
        self.assertNotIn("account_7", {step["resource_id"] for step in steps})

    def test_only_latest_absent_account_readback_is_required(self):
        records = [obs("account_7", "2026-02-01T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}),
                   obs("account_8", "2026-02-20T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}, "evidence_2")]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([step["resource_id"] for step in steps], ["account_8"])

    def test_scope_does_not_replace_direct_account_absence(self):
        records = [
            obs("account_9", "2026-02-01T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}),
            obs("account_8", "2026-02-20T00:00:00Z", "ABSENT", {"lookup_key": "old@example.test"}, "evidence_2"),
            obs("", "2026-02-21T00:00:00Z", "SCOPE", {"resource_prefix": "account_"}, "evidence_scope", "customer_profiles_primary"),
        ]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "COMPLETE")
        self.assertEqual([(step["resource_id"], step["evidence_id"]) for step in steps], [("account_8", "evidence_2")])

    def test_snapshot_scope_must_cover_resource_system(self):
        scope = obs("", "2026-02-20T00:00:00Z", "SCOPE", {"resource_prefix": "account_", "covered_system": "analytics_platform"}, "evidence_scope", "customer_analytics_profiles")
        activity = [{"event_time": "2026-02-01T00:00:00Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "customer_email=old@example.test"}]
        status, steps = predict(REQUEST, SUBJECT, [scope], activity, [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in steps],
                         [("VERIFY_RECORD_ABSENT", "account_7", "")])

    def test_scope_cannot_predate_availability_of_subject_resource_link(self):
        activity = [{"event_time": "2026-02-01T00:00:00Z", "available_at": "2026-02-15T00:00:00Z", "event_type": "DELETE_RECORD", "resource_id": "account_7", "details": "subject_ref=user_1"}]
        scope = obs("", "2026-02-10T00:00:00Z", "SCOPE", {"resource_prefix": "account_", "covered_system": "account_service"}, "evidence_scope", "customer_profiles")
        status, steps = predict(REQUEST, SUBJECT, [scope], activity, [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["resource_id"], s["evidence_id"]) for s in steps],
                         [("VERIFY_RECORD_ABSENT", "account_7", "")])

    def test_scope_does_not_erase_explicit_present_record(self):
        records = [
            obs("account_7", "2026-02-20T00:00:00Z", "PRESENT", {"email": "old@example.test"}),
            obs("", "2026-02-21T00:00:00Z", "SCOPE", {"resource_prefix": "account_"}, "evidence_scope", "customer_profiles_primary"),
        ]
        status, steps = predict(REQUEST, SUBJECT, records, [], [])
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["operation"], "REPORT_REMAINING_RECORD")


if __name__ == "__main__":
    unittest.main()
