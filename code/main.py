"""Build deletion-audit predictions from participant-facing data."""

import argparse
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path

from evidence import load_evidence
from graph import OPERATIONS, STATES, predict
from identity import resolve_identity
from model import LocalModel, load_local_environment

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def validate_predictions(requests, rows):
    case_ids = sorted(request["case_id"] for request in requests)
    if [row["case_id"] for row in rows] != case_ids or len(set(case_ids)) != len(
        case_ids
    ):
        raise ValueError(
            "prediction rows must match requests exactly once in case_id order"
        )
    fields = {
        "step_no",
        "phase",
        "system_id",
        "resource_id",
        "operation",
        "state",
        "evidence_id",
    }
    phase_order = list(OPERATIONS)
    for row in rows:
        status = row["deletion_status"]
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            raise ValueError("invalid deletion_status")
        steps = json.loads(row["steps_json"])
        if not isinstance(steps, list) or (status == "NOT_APPLICABLE" and steps):
            raise ValueError("invalid steps_json")
        if status == "COMPLETE" and not steps:
            raise ValueError("complete prediction needs cited verification")
        prior = (-1, -1, "", "")
        for number, step in enumerate(steps, 1):
            if (
                set(step) != fields
                or step["step_no"] != number
                or step["phase"] not in OPERATIONS
            ):
                raise ValueError("invalid step shape or numbering")
            if (
                step["operation"] not in OPERATIONS[step["phase"]]
                or step["state"] != STATES[step["operation"]]
            ):
                raise ValueError("invalid operation/state")
            ordering = (
                phase_order.index(step["phase"]),
                OPERATIONS[step["phase"]].index(step["operation"]),
                step["system_id"],
                step["resource_id"],
            )
            if ordering < prior:
                raise ValueError("steps are not in contract order")
            prior = ordering
            if status == "COMPLETE" and (
                step["phase"] != "VERIFICATION" or not step["evidence_id"]
            ):
                raise ValueError("complete step needs cited verification")
            if status == "INCOMPLETE" and bool(step["evidence_id"]) != (
                step["phase"] == "FINDING"
            ):
                raise ValueError("finding and proposed-step citation mismatch")


def run(
    dataset=ROOT / "dataset",
    sample=False,
    output=None,
    use_model=True,
    config_path=ROOT / "config.json",
    context=None,
):
    dataset = Path(dataset)
    config = json.loads(Path(config_path).read_text())
    load_local_environment(ROOT)
    if use_model and not os.environ.get("MLX_VLM_API_KEY"):
        raise RuntimeError(
            "MLX_VLM_API_KEY is required; use --no-model for a parser-only baseline"
        )
    model = (
        LocalModel(
            ROOT / ".model-cache", endpoint=config["endpoint"], model=config["model"]
        )
        if use_model
        else None
    )
    started = time.monotonic()
    requests = read_csv(
        dataset
        / ("sample_deletion_requests.csv" if sample else "deletion_requests.csv")
    )
    identities = read_csv(dataset / "user_identities.csv")
    history = read_csv(dataset / "user_audit_log.csv")
    activity = read_csv(dataset / "system_activity.csv")
    rules = read_csv(dataset / "deletion_rules.csv")
    observations = load_evidence(dataset, model)
    if context is not None:
        context.update(observations=observations, activities=activity, requests=requests)
    report_counts = Counter(request["reported_at"] for request in requests)
    rows = []
    for request in sorted(requests, key=lambda row: row["case_id"]):
        request["report_time_ambiguous"] = report_counts[request["reported_at"]] > 1
        if model and not request["request_text"].isascii():
            answer = model.extract(
                "Does this person authorize deletion of their account or personal data? "
                "Return deletion_requested as a JSON boolean. Request: "
                + request["request_text"],
                ["deletion_requested"],
            )
            if not isinstance(answer["deletion_requested"], bool):
                raise ValueError("model deletion_requested must be a boolean")
            request["deletion_authorized"] = answer["deletion_requested"]
        identity = resolve_identity(request, identities, history)
        status, steps = predict(request, identity, observations, activity, rules)
        rows.append(
            {
                "case_id": request["case_id"],
                "deletion_status": status,
                "steps_json": json.dumps(
                    steps, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
    validate_predictions(requests, rows)
    destination = (
        Path(output)
        if output
        else ROOT / ("sample_predictions.csv" if sample else "output.csv")
    )
    with destination.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file, fieldnames=["case_id", "deletion_status", "steps_json"]
        )
        writer.writeheader()
        writer.writerows(rows)
    usage = (
        dict(model.usage)
        if model
        else {
            "calls": 0,
            "cache_hits": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_seconds": 0.0,
        }
    )
    usage.update(
        {
            "provider": config["provider"] if model else "none",
            "model": config["model"] if model else "none",
            "requests": len(rows),
            "wall_seconds": time.monotonic() - started,
            "screenshots_processed": bool(model),
        }
    )
    return rows, usage


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="run a deterministic parser-only baseline",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    rows, usage = run(
        args.dataset, args.sample, args.output, not args.no_model, args.config
    )
    print(
        "wrote",
        len(rows),
        "rows; model calls",
        usage["calls"],
        "cache hits",
        usage["cache_hits"],
    )
