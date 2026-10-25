"""Evidence-linked temporal state and deterministic remediation ordering."""

import re
from collections import defaultdict

from evidence import RESOURCE, system_for

OPERATIONS = {
    "FINDING": [
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
    ],
    "REMEDIATION": [
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
    ],
    "VERIFICATION": [
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED",
    ],
}
STATES = {
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}


def deletion_intent(text):
    value = text.casefold()
    if any(
        x in value
        for x in (
            "do not delete",
            "don't delete",
            "do not close",
            "don't close",
            "not asking for deletion",
            "not a request to erase",
            "without removing",
            "marketing lists only",
            "leave the account active",
            "keep the account",
            "leave my profile",
            "still use it",
            "still need the account",
        )
    ):
        return False
    return any(
        x in value
        for x in (
            "delet",
            "eras",
            "remov",
            "close my account",
            "close this account",
            "close the account",
            "account closed",
            "profile is gone",
            "suppression",
            "exclusão",
            "exclu",
            "supprim",
            "削除",
            "삭제",
            "borrar",
            "elimin",
            "lösch",
            "cancell",
        )
    )


def _all_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, str):
        yield value.casefold()


def _matches(data, identity):
    vals = identity["values"]
    for string in _all_strings(data):
        if string in vals:
            return True
        user_id = str(identity.get("user_id", "")).casefold()
        if user_id and re.search(
            r"(?:\b(?:user_id|subject_ref|subject_reference|customer_reference)\s*[=:]\s*['\"]?|(?<![\w@.]))"
            + re.escape(user_id)
            + r"['\"]?(?:$|[\s,;:!?]|\.(?=$))",
            string,
        ):
            return True
        for value in vals:
            if len(value) >= 7 and re.search(
                r"(?<![\w@.])" + re.escape(value) + r"(?![\w@.])", string
            ):
                return True
    return False


def _has_direct_identifier(data, identity):
    if not isinstance(data, dict):
        return False
    keys = (
        "email",
        "phone",
        "email_hash",
        "full_name",
        "display_name",
        "requester_name",
        "requester_email",
        "requester_phone",
        "customer_email",
        "owner_email",
        "owner_phone",
        "name",
        "visible_email",
        "visible_name",
        "visible_phone",
    )
    if any(data.get(key) not in (None, "", "null") for key in keys):
        return True
    raw = str(data.get("raw", "")).casefold()
    if (
        "identifiable" in raw
        or "direct_identifiers=1" in raw
        or "matched_value=" in raw
    ):
        return True
    return False


def _policy(resource, observation, rules, cutoff):
    data = observation["data"]
    prefix = resource.split("_")[0]
    if resource.startswith("data_flow_"):
        return "FLOW"
    if prefix == "backup":
        return "BACKUP"
    category = str(data.get("data_category", data.get("record_type", ""))).upper()
    if category not in {row["data_category"] for row in rules}:
        category = ""
    if prefix == "support":
        if not category:
            category = (
                "SUPPORT_ATTACHMENT"
                if "attachment_pii_scan" in str(data) or "contact_query" in str(data)
                else "SUPPORT_MESSAGE"
            )
    if prefix == "analytics" and not category:
        category = (
            "SECONDARY_IDENTIFIER"
            if "contact_query" in str(data) or "contact_lookup" in str(data)
            else "ANALYTICS_PROFILE"
        )
    if not category:
        category = {
            "account": "USER_PROFILE",
            "search": "SEARCH_INDEX_ENTRY",
            "export": "USER_EXPORT",
            "tombstone": "ACCOUNT_TOMBSTONE",
            "audit": "SECURITY_AUDIT_LOG",
            "marketing": "MARKETING_PROFILE",
            "email": "EMAIL_DELIVERY_LOG",
            "cache": "CACHE_ENTRY",
        }.get(prefix, "SECONDARY_IDENTIFIER")
    for rule in rules:
        if rule["data_category"] == category:
            action = rule["required_action"]
            condition = rule.get("condition", "").casefold()
            if action == "RETAIN" and "retention period" in condition:
                until = data.get("retention_until")
                return "DELETE" if until and str(until) < cutoff else action
            if action == "RETAIN" and "legal hold" in condition:
                return "DELETE" if data.get("hold_active") is False else action
            if action == "REDACT" and "shared" in condition:
                return "DELETE" if data.get("shared_context") is False else action
            if action == "DELETE" and "belongs only" in condition:
                return "REDACT" if data.get("subject_only") is False else action
            return action
    return {
        "SUPPORT_MESSAGE": "REDACT",
        "ANALYTICS_PROFILE": "ANONYMIZE",
        "ACCOUNT_TOMBSTONE": "REDACT",
        "SECURITY_AUDIT_LOG": "RETAIN",
        "EMAIL_DELIVERY_LOG": "REDACT",
    }.get(category, "DELETE")


def _compliant(policy, observation, identity):
    state, data = observation["state"], observation["data"]
    if policy == "DELETE":
        return state == "ABSENT"
    if policy == "RETAIN":
        return state == "PRESENT"
    if policy == "FLOW":
        return str(data.get("enabled", "")).lower() in (
            "false",
            "0",
        ) or "enabled=false" in str(data.get("raw", ""))
    if policy == "BACKUP":
        return str(data.get("restore_allowed", "")).lower() in (
            "false",
            "0",
        ) or "restore_allowed=false" in str(data.get("raw", ""))
    if policy in ("REDACT", "ANONYMIZE"):
        raw = str(data.get("raw", "")).upper()
        linked = policy == "ANONYMIZE" and any(
            data.get(key) not in (None, "", "null")
            for key in ("user_id", "customer_reference", "subject_reference")
        )
        return (
            state == "PRESENT"
            and not linked
            and not _has_direct_identifier(data, identity)
            and ("IDENTIFIABLE" not in raw)
        )
    return False


def _step(phase, operation, resource, evidence_id=""):
    return {
        "phase": phase,
        "system_id": system_for(resource),
        "resource_id": resource,
        "operation": operation,
        "state": STATES[operation],
        "evidence_id": evidence_id,
    }


def _future_verification(resource, rules, cutoff):
    if not system_for(resource):
        return None
    policy = _policy(resource, {"state": "ABSENT", "data": {}}, rules, cutoff)
    return {
        "DELETE": "VERIFY_RECORD_ABSENT",
        "REDACT": "VERIFY_RECORD_REDACTED",
        "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
        "RETAIN": "VERIFY_RECORD_PRESENT",
        "FLOW": "VERIFY_DATA_FLOW_DISABLED",
        "BACKUP": "VERIFY_RESTORE_BLOCKED",
    }.get(policy)


def predict(request, identity, observations, activities, rules):
    if not request.get("deletion_authorized", deletion_intent(request["request_text"])):
        return "NOT_APPLICABLE", []
    cutoff = request["audit_at"]
    eligible = [
        o
        for o in observations
        if o["time"] <= cutoff and o.get("available_at", o["time"]) <= cutoff
    ]
    scopes = [o for o in eligible if o["state"] == "SCOPE"]
    records = [o for o in eligible if o["state"] != "SCOPE"]
    by_id = defaultdict(list)
    for row in records:
        by_id[row["resource_id"]].append(row)
    links = defaultdict(list)

    def link(resource, when, available_at, source, kind):
        if not resource:
            return
        links[resource].append(
            {
                "time": when,
                "available_at": max(when, available_at or when),
                "source": source,
                "kind": kind,
            }
        )

    for row in records:
        if _matches(row["data"], identity):
            link(
                row["resource_id"],
                row["time"],
                row.get("available_at", row["time"]),
                row["evidence_id"],
                "observation",
            )
    for event in activities:
        if (
            event["event_time"] <= cutoff
            and event.get("available_at", event["event_time"]) <= cutoff
            and _matches(event.get("details", ""), identity)
        ):
            link(
                event["resource_id"],
                event["event_time"],
                event.get("available_at", event["event_time"]),
                event.get("activity_id", event["event_type"]),
                event["event_type"],
            )
    linked = set(links)
    pending = list(linked)
    while pending:
        source = pending.pop()
        for row in by_id[source]:
            for key in ("source_resource_id", "related_resource_id"):
                reference = row["data"].get(key) if isinstance(row["data"], dict) else None
                if reference and RESOURCE.fullmatch(str(reference)) and reference not in linked:
                    link(
                        reference,
                        row["time"],
                        row.get("available_at", row["time"]),
                        row["evidence_id"],
                        "resource_reference",
                    )
                    linked.add(reference)
                    pending.append(reference)
    by_resource = defaultdict(list)
    for obs in records:
        if obs["resource_id"] in linked:
            by_resource[obs["resource_id"]].append(obs)
    for resource in linked:
        for scope in scopes:
            link_info = min(
                links[resource],
                key=lambda value: (value["available_at"], value["time"]),
            )
            scope_system = scope["data"].get("covered_system")
            if (
                resource.startswith(scope["data"]["resource_prefix"])
                and (not scope_system or scope_system == system_for(resource))
                and scope["time"] >= link_info["time"]
                and scope.get("available_at", scope["time"]) >= link_info["available_at"]
            ):
                covered_store = scope["data"].get("covered_store", scope["store"])
                store_name = str(covered_store).lower()
                scope_store = (
                    store_name
                    if any(name in store_name for name in ("replica", "failover"))
                    else system_for(resource)
                )
                if any(
                    (
                        row["store"]
                        if any(name in row["store"].lower() for name in ("replica", "failover"))
                        else system_for(resource)
                    )
                    == scope_store
                    for row in by_id[resource]
                ):
                    continue
                by_resource[resource].append(
                    {
                        **scope,
                        "resource_id": resource,
                        "state": "ABSENT",
                        "data": {"resource_id": resource},
                    }
                )
    absent_accounts = [
        (max(row["time"] for row in records), resource)
        for resource, records in by_resource.items()
        if resource.startswith("account_")
        and all(row["state"] == "ABSENT" for row in records)
    ]
    if len(absent_accounts) > 1:
        newest = max(absent_accounts)[1]
        for _, resource in absent_accounts:
            if resource != newest:
                del by_resource[resource]
                linked.discard(resource)
    findings, remediation, verification = [], [], []
    missing_required_record = False
    for resource, records in by_resource.items():
        latest_by_store = {}
        for row in records:
            store = (
                row["store"]
                if any(name in row["store"].lower() for name in ("replica", "failover"))
                else system_for(resource)
            )
            if store not in latest_by_store or (row["time"], row["evidence_id"]) > (
                latest_by_store[store]["time"],
                latest_by_store[store]["evidence_id"],
            ):
                latest_by_store[store] = row
        current = list(latest_by_store.values())
        current.sort(
            key=lambda row: (
                row["state"] == "PRESENT",
                row["time"],
                row["evidence_id"],
            ),
            reverse=True,
        )
        observed = current[0]
        policy = _policy(resource, observed, rules, cutoff)
        if policy == "DELETE" and all(row["state"] == "ABSENT" for row in current):
            observed = max(current, key=lambda row: (row["time"], row["evidence_id"]))
        compliant = _compliant(policy, observed, identity)
        if policy == "FLOW":
            fix, check, finding = (
                "DISABLE_DATA_FLOW",
                "VERIFY_DATA_FLOW_DISABLED",
                "REPORT_ACTIVE_DATA_FLOW",
            )
        elif policy == "BACKUP":
            fix, check, finding = (
                "BLOCK_RESTORE",
                "VERIFY_RESTORE_BLOCKED",
                "REPORT_RESTORE_RISK",
            )
        elif policy == "REDACT":
            fix, check, finding = (
                "REDACT_RECORD",
                "VERIFY_RECORD_REDACTED",
                "REPORT_EXPOSED_PII",
            )
        elif policy == "ANONYMIZE":
            fix, check, finding = (
                "ANONYMIZE_RECORD",
                "VERIFY_RECORD_ANONYMIZED",
                "REPORT_EXPOSED_PII",
            )
        elif policy == "RETAIN":
            fix, check, finding = None, "VERIFY_RECORD_PRESENT", None
        else:
            fix, check, finding = (
                "DELETE_RECORD",
                "VERIFY_RECORD_ABSENT",
                "REPORT_REMAINING_RECORD",
            )
        if (
            policy in ("RETAIN", "REDACT", "ANONYMIZE")
            and observed["state"] == "ABSENT"
        ):
            missing_required_record = True
            verification.append(_step("VERIFICATION", check, resource))
            continue
        if resource.startswith("tombstone_") and not any(
            observed["data"].get(key) == identity["user_id"]
            for key in ("user_id", "subject_reference", "customer_reference")
        ):
            missing_required_record = True
            verification.append(_step("VERIFICATION", check, resource))
            continue
        if compliant:
            verification.append(
                _step("VERIFICATION", check, resource, observed["evidence_id"])
            )
        elif fix:
            findings.append(
                _step("FINDING", finding, resource, observed["evidence_id"])
            )
            remediation.append(_step("REMEDIATION", fix, resource))
            verification.append(_step("VERIFICATION", check, resource))
    missing_links = linked - set(by_resource)
    for resource in sorted(missing_links):
        evidence_links = links[resource]
        if all(link_info["kind"] == "LINK_RESOURCE_IDENTITY" for link_info in evidence_links):
            continue
        operation = _future_verification(resource, rules, cutoff)
        if operation:
            verification.append(_step("VERIFICATION", operation, resource))
    incomplete = bool(findings) or missing_required_record or bool(missing_links) or not verification
    if incomplete:
        for step in verification:
            step["evidence_id"] = ""
    steps = findings + remediation + verification
    steps.sort(
        key=lambda step: (
            list(OPERATIONS).index(step["phase"]),
            OPERATIONS[step["phase"]].index(step["operation"]),
            step["system_id"],
            step["resource_id"],
        )
    )
    for number, step in enumerate(steps, 1):
        step["step_no"] = number
    return ("INCOMPLETE" if incomplete else "COMPLETE"), steps
