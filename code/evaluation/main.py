"""Evaluate only against public samples and write submission reports."""

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "code"
sys.path.insert(0, str(CODE))


def rows(path):
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def metrics(expected, actual):
    predicted = {row["case_id"]: row for row in actual}
    total = len(expected)
    status_hits = sequence_hits = matched_steps = expected_steps = predicted_steps = (
        field_hits
    ) = field_total = 0
    fields = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
    missing = []
    status_mismatch_cases = []
    for truth in expected:
        guess = predicted.get(truth["case_id"])
        if guess is None:
            missing.append(truth["case_id"])
            continue
        status_match = guess["deletion_status"] == truth["deletion_status"]
        status_hits += status_match
        if not status_match:
            status_mismatch_cases.append(truth["case_id"])
        wanted, got = json.loads(truth["steps_json"]), json.loads(guess["steps_json"])
        sequence_hits += wanted == got
        expected_steps += len(wanted)
        predicted_steps += len(got)
        keys = lambda steps: Counter(
            tuple(step.get(field) for field in fields) for step in steps
        )
        matched_steps += sum((keys(wanted) & keys(got)).values())
        for index in range(max(len(wanted), len(got))):
            for field in fields:
                field_total += 1
                field_hits += (
                    index < len(wanted)
                    and index < len(got)
                    and wanted[index].get(field) == got[index].get(field)
                )
    return {
        "cases": total,
        "status_accuracy": status_hits / total if total else 0,
        "step_precision": matched_steps / predicted_steps if predicted_steps else 0,
        "step_recall": matched_steps / expected_steps if expected_steps else 0,
        "field_accuracy": field_hits / field_total if field_total else 0,
        "sequence_accuracy": sequence_hits / total if total else 0,
        "missing_cases": missing,
        "status_mismatch_cases": status_mismatch_cases,
    }


def unavailable_expected_steps(expected, requests, observations, activities):
    cutoffs = {request["case_id"]: request["audit_at"] for request in requests}
    by_resource = {}
    for observation in observations:
        by_resource.setdefault(observation["resource_id"], []).append(
            observation.get("available_at") or observation.get("time")
        )
    for event in activities:
        by_resource.setdefault(event["resource_id"], []).append(
            event.get("available_at", event["event_time"])
        )
    result = []
    for case in expected:
        cutoff = cutoffs[case["case_id"]]
        for step in json.loads(case["steps_json"]):
            times = by_resource.get(step["resource_id"], [])
            if any(time <= cutoff for time in times):
                continue
            result.append(
                {
                    "case_id": case["case_id"],
                    "resource_id": step["resource_id"],
                    "operation": step["operation"],
                    "availability": "after_audit" if times else "not_found",
                }
            )
    return result


def write_reports(report_dir, results, usage, output_rows, unavailable_steps=None):
    report_dir.mkdir(parents=True, exist_ok=True)
    total_tokens = usage["input_tokens"] + usage["output_tokens"]
    requests = usage["requests"]
    evaluation = f"""# Public Sample Evaluation

Predictions use only participant-facing inputs. `sample_output.csv` is read here solely for scoring. The 32 public samples are disjoint from the 518 evaluation requests; hidden accuracy remains unknown.

| Metric | Result |
| --- | ---: |
| Case deletion-status accuracy | {results['status_accuracy']:.1%} |
| Step precision (unordered exact fields) | {results['step_precision']:.1%} |
| Step recall (unordered exact fields) | {results['step_recall']:.1%} |
| Field accuracy (position aligned) | {results['field_accuracy']:.1%} |
| Exact sequence accuracy | {results['sequence_accuracy']:.1%} |

Step comparison includes phase, system, resource, operation, state, and evidence. Field accuracy compares the same six fields at each array position; missing or extra positions count as wrong. Sequence accuracy requires the entire ordered step array to match.

## Additional Participant-Created Cases

The local regression suite includes three synthetic cases: a correction request with a deletion-like workflow claim remains `NOT_APPLICABLE`; a failover replica containing a profile overrides a primary absence and remains `INCOMPLETE`; and a tombstone retaining only the stable user reference is `COMPLETE`. See `tests/test_graph.py` for fixtures and assertions.

## Failures Found and Improvements Made

- Earlier parsing missed JSON-formatted log readbacks; they now produce resource-scoped observations.
- File capture time now gates nested record timestamps, preventing later exports from affecting earlier audits.
- PDF support attachments and historical email hashes now contribute evidence.
- Replica presence takes priority over primary absence; active flows and restore risks precede record changes.
- Explicit resource links and scoped SQL/JSON snapshots recover some identifier-free verification steps; missing proof now yields `INCOMPLETE` rather than an empty `COMPLETE` answer.
- Public status misses: {', '.join(results.get('status_mismatch_cases', [])) or 'none'}. Requiring a subject link may lower public status accuracy when a nearby deletion event does not identify its subject; reported workflow status is not used as a substitute.
- Public step recall is {results['step_recall']:.1%}; the 85% acceptance target is {'met' if results['step_recall'] > 0.85 else 'not met'} with the current evidence links.
- Conditional retention and shared-record rules use supplied record facts. Exact sequence accuracy remains limited when an anonymous or absent record has no grounded subject link; the public metric is not a hidden-score claim.
- Two full-dataset requests still have `INCOMPLETE` with no steps because the available evidence does not establish a case-specific resource link. They remain unresolved rather than claiming completion without proof.

The full output contains {len(output_rows)} rows, sorted by `case_id`.
"""
    evaluation += "\n## Expected Resources Not Available by audit_at\n\n"
    if unavailable_steps is None:
        evaluation += "Not computed for this parser-only run.\n"
    elif unavailable_steps:
        evaluation += "| Case | Resource | Expected check | Availability |\n| --- | --- | --- | --- |\n"
        evaluation += "\n".join(
            f"| {row['case_id']} | {row['resource_id']} | {row['operation']} | {row['availability']} |"
            for row in unavailable_steps
        )
        evaluation += "\n"
    else:
        evaluation += "All expected sample resources appeared in participant inputs by their audit cutoffs.\n"
    (report_dir / "evaluation_report.md").write_text(evaluation, encoding="utf-8")
    usage_text = f"""# Final Full-Dataset Run Usage

| Provider | Model | Calls | Input tokens | Output tokens | Total tokens | Estimated cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| {usage['provider']} | {usage['model']} | {usage['calls']} | {usage['input_tokens']} | {usage['output_tokens']} | {total_tokens} | $0.00 |
| Overall | — | {usage['calls']} | {usage['input_tokens']} | {usage['output_tokens']} | {total_tokens} | $0.00 |

- Requests: {requests}; average tokens per request: {total_tokens / requests if requests else 0:.2f}.
- Estimated total cost: $0.00; estimated cost per request: $0.00 (local inference, excluding hardware and electricity).
- Cached responses reused: {usage['cache_hits']}; inference elapsed: {usage['elapsed_seconds']:.2f}s; full run elapsed: {usage['wall_seconds']:.2f}s.
- Screenshot extraction performed: {'yes' if usage['screenshots_processed'] else 'no'}.

Token counts are those returned by the local server. If it omits usage fields, reported token counts are incomplete; no estimate is presented as measured usage.
"""
    (report_dir / "usage_report.md").write_text(usage_text, encoding="utf-8")


def evaluate(report_dir=None, use_model=True):
    config = json.loads((ROOT / "config.json").read_text())
    destination = Path(report_dir) if report_dir else ROOT / config["evaluation_dir"]
    spec = importlib.util.spec_from_file_location("prediction_runner", CODE / "main.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    output_rows, usage = runner.run(use_model=use_model)
    sample_context = {}
    sample_rows, _ = runner.run(
        sample=True, use_model=use_model, context=sample_context
    )
    sample_truth = rows(ROOT / "dataset" / "sample_output.csv")
    result = metrics(sample_truth, sample_rows)
    unavailable = (
        unavailable_expected_steps(
            sample_truth,
            sample_context["requests"],
            sample_context["observations"],
            sample_context["activities"],
        )
        if use_model
        else None
    )
    write_reports(destination, result, usage, output_rows, unavailable)
    return result, usage


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-dir", type=Path)
    parser.add_argument("--no-model", action="store_true")
    args = parser.parse_args()
    score, usage = evaluate(args.evaluation_dir, not args.no_model)
    print(
        "public status accuracy",
        f"{score['status_accuracy']:.1%}",
        "exact sequence",
        f"{score['sequence_accuracy']:.1%}",
        "model calls",
        usage["calls"],
    )
