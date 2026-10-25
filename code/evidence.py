"""Normalize participant-visible files into timestamped resource observations."""

import csv
import json
import re
import sqlite3
from pathlib import Path

SYSTEM_BY_PREFIX = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "marketing": "marketing_platform",
    "email_delivery": "email_delivery_service",
    "cache": "cache_service",
}

SCOPE_PREFIXES = {
    "customer_profiles": "account_",
    "customer_search_documents": "search_",
    "customer_analytics_profiles": "analytics_",
    "support_records": "support_",
    "customer_exports": "export_",
    "connector_subscriptions": "data_flow_",
    "backup_restore_entries": "backup_",
    "security_audit_records": "audit_record_",
}


def system_for(resource_id):
    for prefix, system in sorted(
        SYSTEM_BY_PREFIX.items(), key=lambda pair: -len(pair[0])
    ):
        if resource_id.startswith(prefix + "_"):
            return system
    return ""


def observation(
    resource_id,
    evidence_id,
    time,
    data,
    store="",
    system_id="",
    state="",
    available_at=None,
):
    if not resource_id:
        return None
    if not state:
        if (
            data is None
            or isinstance(data, dict)
            and (
                data.get("status_code") == 404
                or data.get("rows") == "0"
                or data.get("result") in ("miss", "NOT_FOUND")
            )
        ):
            state = "ABSENT"
        else:
            state = "PRESENT"
    return {
        "resource_id": str(resource_id),
        "system_id": system_id or system_for(str(resource_id)),
        "evidence_id": evidence_id,
        "time": time,
        "available_at": available_at or time,
        "state": state,
        "store": store,
        "data": data or {},
    }


def snapshot_scope(
    collection, evidence_id, captured_at, covered_store="", available_at=None
):
    prefix = next(
        (
            value
            for name, value in SCOPE_PREFIXES.items()
            if collection.startswith(name)
        ),
        None,
    )
    if prefix is None:
        return None
    covered_system = system_for(prefix + "0")
    store = covered_store or collection
    row = observation(
        "_scope_",
        evidence_id,
        captured_at,
        {
            "resource_prefix": prefix,
            "covered_system": covered_system,
            "covered_store": store,
        },
        store,
        covered_system,
        state="SCOPE",
        available_at=available_at,
    )
    row["resource_id"] = ""
    return row


def parse_json(path, evidence_id, captured_at):
    doc = json.loads(path.read_text())
    result = []
    if "snapshots" in doc:
        for snap in doc["snapshots"]:
            scope = snapshot_scope(
                snap.get("collection", ""),
                evidence_id,
                snap.get("captured_at", captured_at),
                snap.get("data_store", ""),
                captured_at,
            )
            if scope:
                result.append(scope)
            for row in snap.get("records", []):
                result.append(
                    observation(
                        row.get("resource_id"),
                        evidence_id,
                        snap.get("captured_at", captured_at),
                        row,
                        snap.get("collection", ""),
                        snap.get("data_store", ""),
                    )
                )
    elif "service_exports" in doc:
        for system, groups in doc["service_exports"].items():
            for group, rows in groups.items():
                for row in rows:
                    data = row.copy()
                    if group == "index_queries":
                        hits = row.get("hits") or {}
                        data.update(
                            (hits.get("documents") or [{}])[0]
                            if hits.get("total")
                            else {}
                        )
                        state = "PRESENT" if hits.get("total") else "ABSENT"
                    elif group == "profile_lookups":
                        data.update(row.get("profile") or {})
                        state = "ABSENT" if row.get("status_code") == 404 else "PRESENT"
                    else:
                        state = "PRESENT"
                    result.append(
                        observation(
                            row.get("resource_id"),
                            evidence_id,
                            row.get("observed_at", captured_at),
                            data,
                            group,
                            system,
                            state,
                        )
                    )
    else:
        for row in doc.get("records", []):
            result.append(
                observation(
                    row.get("resource_id"),
                    evidence_id,
                    row.get("observed_at", doc.get("captured_at", captured_at)),
                    row,
                    doc.get("collection", ""),
                    row.get("system_id", ""),
                )
            )
    return [x for x in result if x]


def parse_sql(path, evidence_id, captured_at):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(path.read_text())
    result = []
    try:
        scope_times = {}
        if "snapshot_scope" in {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            scope_times = {
                row["origin_system"]: row["captured_at"]
                for row in db.execute('SELECT * FROM "snapshot_scope"')
            }
        tables = [
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            if table == "snapshot_scope":
                continue
            prefix = next(
                (
                    value
                    for name, value in SCOPE_PREFIXES.items()
                    if table.startswith(name)
                ),
                None,
            )
            covered_system = system_for(prefix + "0") if prefix else ""
            covered_store = covered_system if covered_system in scope_times else table
            scope = snapshot_scope(
                table,
                evidence_id,
                scope_times.get(covered_system, captured_at),
                covered_store,
                captured_at,
            )
            if scope:
                result.append(scope)
            for raw in db.execute('SELECT * FROM "' + table + '"'):
                row = dict(raw)
                data = json.loads(row["record_json"]) if "record_json" in row else row
                data.setdefault("resource_id", row.get("resource_id"))
                system = row.get("origin_system") or system_for(data["resource_id"])
                result.append(
                    observation(
                        data["resource_id"],
                        evidence_id,
                        row.get("updated_at", captured_at),
                        data,
                        table,
                        system,
                    )
                )
    finally:
        db.close()
    return result


KV = re.compile(r'(\w+)=(?:\\?"([^"\\]*(?:\\.[^"\\]*)*)\\?"|([^\s]+))')
RESOURCE = re.compile(
    r"\b(?:account|search|support|analytics|export|data_flow|backup|audit_record|tombstone|marketing|email_delivery|cache)_[0-9]+\b"
)
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def parse_text(path, evidence_id, captured_at, model=None):
    result = []
    traces = {}
    for line in path.read_text(errors="replace").splitlines():
        fields = {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in KV.finditer(line)
        }
        if "{" in line:
            try:
                structured = json.loads(line[line.index("{") :])
                fields.update(structured)
                if isinstance(structured.get("message"), str):
                    fields.update(
                        {
                            m.group(1): (
                                m.group(2) if m.group(2) is not None else m.group(3)
                            )
                            for m in KV.finditer(structured["message"])
                        }
                    )
            except json.JSONDecodeError:
                pass
        if isinstance(fields.get("message"), str):
            nested = fields["message"].replace('\\"', '"')
            fields.update(
                {
                    m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
                    for m in KV.finditer(nested)
                }
            )
        if " | " in line:
            parts = [piece.strip() for piece in line.split(" | ")]
            if len(parts) >= 5 and parts[3].startswith("tr-"):
                fields.setdefault("service", parts[2])
                fields.setdefault("trace_id", parts[3])
                fields.setdefault("event", parts[4])
        bare_trace = re.search(r"\btr-[0-9]+\b", line)
        trace = (
            fields.get("trace_id")
            or fields.get("requestId")
            or (bare_trace.group() if bare_trace else None)
        )
        if trace:
            identifiers = {
                key: fields[key]
                for key in (
                    "lookup_key",
                    "match_key",
                    "subject_key",
                    "subject_ref",
                    "correlation_key",
                )
                if fields.get(key)
            }
            if identifiers:
                traces.setdefault(trace, {}).update(identifiers)
        matches = RESOURCE.findall(line)
        if not matches:
            continue
        if trace:
            for key, value in traces.get(trace, {}).items():
                fields.setdefault(key, value)
        event = fields.get("event", "")
        if event.endswith("_started") or "export_started" in event:
            continue
        time = TIME.search(line).group() if TIME.search(line) else captured_at
        resource = fields.get("resource_id") or matches[0]
        if resource not in matches:
            resource = matches[0]
        state = ""
        if (
            str(fields.get("rows")) == "0"
            or str(fields.get("http_status")) == "404"
            or str(fields.get("result", "")).lower() in ("miss", "not_found")
            or "| NOT_FOUND |" in line
        ):
            state = "ABSENT"
        elif (
            any(
                x in line
                for x in (
                    "rows=1",
                    "result=hit",
                    "| FOUND |",
                    "| ACTIVE |",
                    "| IDENTIFIABLE |",
                    "| ANONYMIZED |",
                    "| REDACTED |",
                    "matched_value=",
                    "enabled=true",
                    "restore_allowed=true",
                )
            )
            or "_completed" in event
            or any(
                x in event
                for x in ("scan", "heartbeat", "record_lookup", "restore_candidate")
            )
        ):
            state = "PRESENT"
        elif model is not None and (
            fields.get("rows") == "1"
            or str(fields.get("result", "")).lower() == "hit"
            or str(fields.get("direct_identifiers", "")) == "0"
            or str(fields.get("enabled", "")).lower() == "false"
            or str(fields.get("restore_allowed", "")).lower() == "false"
            or str(fields.get("message_text", "")).upper() == "[REDACTED]"
            or (resource.startswith("backup_") and "| BLOCKED |" in line)
            or (resource.startswith("data_flow_") and "| DISABLED |" in line)
        ):
            answer = model.extract(
                "Does this final log readback prove the named resource exists at this time? "
                "Return state PRESENT or UNKNOWN. Do not treat attempted operations or "
                "workflow claims as proof. Log line: " + line,
                ["state"],
            )
            if answer["state"] != "PRESENT":
                continue
            if resource.startswith("backup_") and "| BLOCKED |" in line:
                fields["restore_allowed"] = "false"
            if resource.startswith("data_flow_") and "| DISABLED |" in line:
                fields["enabled"] = "false"
            state = "PRESENT"
        else:
            continue
        if " | " in line:
            parts = [piece.strip() for piece in line.split("|")]
            if len(parts) >= 5:
                fields["record_type"] = parts[3]
                fields["result"] = parts[4]
        fields["raw"] = line
        result.append(
            observation(
                resource,
                evidence_id,
                time,
                fields,
                "text_readback",
                fields.get("service", fields.get("system", "")),
                state or "PRESENT",
            )
        )
    return result


def parse_pdf(path, evidence_id, captured_at, catalog_links=()):
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    result = []
    for resource in sorted(set(RESOURCE.findall(text))):
        category = "SUPPORT_ATTACHMENT" if resource.startswith("support_") else ""
        result.append(
            observation(
                resource,
                evidence_id,
                captured_at,
                {"raw": text, "record_type": category},
                "document",
            )
        )
    for alias, resource, linked_at in catalog_links:
        if (
            not alias.startswith("user_")
            and alias != resource
            and alias in text
            and resource not in {row["resource_id"] for row in result}
        ):
            row = observation(
                resource, evidence_id, captured_at, {"raw": text}, "document"
            )
            row["available_at"] = max(captured_at, linked_at)
            result.append(row)
    return result


def parse_image(path, evidence_id, captured_at, model):
    prompt = (
        "Read this screenshot. Extract ONLY IDs literally shown with an underscore prefix such as "
        "account_5762 or support_9538. Ignore order numbers (ORD-...), names, navigation, buttons and counts. "
        "For each ID, give resource_id, state PRESENT or ABSENT, and visible email/user_id if it belongs to that ID. "
        'Do not invent an ID. Return {"records":[]} if none.'
    )
    records = model.extract(prompt, ["records"], image_path=path)["records"]
    result = []
    for row in records:
        if not isinstance(row, dict) or not RESOURCE.fullmatch(
            str(row.get("resource_id", ""))
        ):
            continue
        state = (
            row.get("state") if row.get("state") in ("PRESENT", "ABSENT") else "PRESENT"
        )
        result.append(
            observation(
                row["resource_id"],
                evidence_id,
                captured_at,
                row,
                "screenshot",
                row.get("system_id", ""),
                state,
            )
        )
    return result


def load_evidence(dataset_path, model=None):
    result = []
    dataset_path = Path(dataset_path)
    catalog_links = []
    with (dataset_path / "system_activity.csv").open() as f:
        for event in csv.DictReader(f):
            if event["event_type"] == "LINK_RESOURCE_IDENTITY":
                match = re.search(r"\(([^)]+)\)", event["details"])
                if match:
                    catalog_links.append(
                        (match.group(1), event["resource_id"], event["event_time"])
                    )
    with (dataset_path / "evidence_files.csv").open() as f:
        manifest = list(csv.DictReader(f))
    for item in manifest:
        path = Path(dataset_path) / item["file_path"]
        suffix = path.suffix.lower()
        if suffix == ".json":
            parsed = parse_json(path, item["file_id"], item["captured_at"])
        elif suffix == ".sql":
            parsed = parse_sql(path, item["file_id"], item["captured_at"])
        elif suffix in (".log", ".txt"):
            parsed = parse_text(path, item["file_id"], item["captured_at"], model)
        elif suffix == ".pdf":
            parsed = parse_pdf(
                path, item["file_id"], item["captured_at"], catalog_links
            )
        elif suffix in (".png", ".jpg", ".jpeg") and model is not None:
            parsed = parse_image(path, item["file_id"], item["captured_at"], model)
        else:
            parsed = []
        for row in parsed:
            row["available_at"] = max(row["available_at"], item["captured_at"])
        result.extend(parsed)
    return result
