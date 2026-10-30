"""Synthetic payload validation; no agents, business tools, files or model calls."""

from decimal import Decimal, localcontext
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from office_agents.schemas import (
    ContractError, EvidenceBundle, Metric, ReportDraft, ReportPlan, ReviewIssue,
    ReviewResult, SourceRef, TaskSpec, validate_draft, validate_evidence,
    validate_plan, validate_review,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/contracts"
CLASSES = {"task": TaskSpec, "plan": ReportPlan, "evidence": EvidenceBundle,
           "draft": ReportDraft, "review": ReviewResult, "source": SourceRef}


@pytest.fixture
def payload():
    return json.loads((EXAMPLES / "valid.json").read_text())


def parse(model, value):
    return model.model_validate_json(json.dumps(value, ensure_ascii=False))


def objects(payload):
    return {key: parse(CLASSES[key], payload[key]) for key in ("task", "plan", "evidence", "draft", "review")}


def issue(**overrides):
    return {"issue_id": "I001", "severity": "blocker", "category": "metric",
            "section_id": "progress", "claim_id": "C001", "expected": "60",
            "actual": "75", "suggestion": "按证据修正数值", **overrides}


def check_draft(o):
    validate_draft(o["draft"], o["plan"], o["evidence"],
                   plan_ref="artifacts/plan-r0.json", evidence_ref="artifacts/evidence-r0.json")


def check_review(o, **overrides):
    kwargs = {"draft_ref": "artifacts/draft-r0.json", "draft_sha256": "c" * 64,
              "evidence_ref": "artifacts/evidence-r0.json", **overrides}
    validate_review(o["review"], o["draft"], o["plan"], **kwargs)


def test_legal_handoffs_and_lossless_json_roundtrip(payload):
    o = objects(payload)
    validate_plan(o["task"], o["plan"])
    validate_evidence(o["plan"], o["evidence"])
    check_draft(o)
    check_review(o)
    assert o["task"].ready_for_planner and o["evidence"].ready_for_writer
    assert o["review"].repair_target == "none"
    assert o["evidence"].metrics[0].raw_value == Decimal("60")
    for key, model in o.items():
        assert CLASSES[key].model_validate_json(model.model_dump_json()) == model
        assert model.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("key", ["task", "plan", "evidence", "draft", "review"])
@pytest.mark.parametrize("change", [{"unknown": "extra"}, {"schema_version": "2.0"}])
def test_contracts_reject_extra_fields_and_unknown_versions(payload, key, change):
    with pytest.raises(ValidationError):
        parse(CLASSES[key], {**payload[key], **change})


def test_invalid_examples_are_explicitly_rejected():
    for case in json.loads((EXAMPLES / "invalid.json").read_text())["cases"]:
        with pytest.raises(ValidationError, match=case["expected_error"]):
            parse(CLASSES[case["kind"]], case["value"])


def test_missing_task_is_valid_but_cannot_advance(payload):
    task = TaskSpec.model_validate_json((EXAMPLES / "needs-input.json").read_text())
    assert not task.ready_for_planner
    with pytest.raises(ContractError, match="needs input"):
        validate_plan(task, parse(ReportPlan, payload["plan"]))


@pytest.mark.parametrize("change", [
    {"department": None}, {"department": " "}, {"period": "2026-Q5"}, {"output_format": "pdf"},
    {"missing_fields": ["period"]}, {"input_file_ids": ["projects", "projects"]},
    {"required_sections": ["项目进度", "项目进度"]}, {"input_file_ids": []},
])
def test_task_missingness_format_and_unique_lists(payload, change):
    with pytest.raises(ValidationError):
        parse(TaskSpec, {**payload["task"], **change})


@pytest.mark.parametrize("change", [{"title": "改进建议"}, {"required": False}])
def test_plan_cannot_drop_or_demote_user_requirement(payload, change):
    payload["plan"]["sections"][0].update(change)
    o = objects(payload)
    with pytest.raises(ContractError, match="required section"):
        validate_plan(o["task"], o["plan"])


def test_plan_rejects_duplicate_sections_and_unknown_metric_keys(payload):
    section = payload["plan"]["sections"][0]
    for sections in ([section, section], [{**section, "required_metrics": ["invented_metric"]}],
                     [{**section, "required_metrics": ["project_count", "project_count"]}]):
        with pytest.raises(ValidationError):
            parse(ReportPlan, {"sections": sections})


@pytest.mark.parametrize("path", [
    "../inputs/projects.csv", "/inputs/projects.csv", "inputs/../projects.csv",
    "inputs/../../secret", "inputs//projects.csv", "inputs/./projects.csv", "inputs",
    "inputs/projects.csv/", r"inputs\projects.csv", "https://example.com/x", "C:/inputs/x",
    "artifacts/projects.csv", "inputs/x?token=x", "inputs/x\n.csv",
])
def test_source_paths_stay_canonical_and_relative(payload, path):
    source = payload["evidence"]["metrics"][0]["source_refs"][0]
    with pytest.raises(ValidationError):
        parse(SourceRef, {**source, "snapshot_path": path})


@pytest.mark.parametrize("change", [
    {"sha256": "not-a-hash"}, {"line_range": [6, 2]}, {"line_range": [0, 2]},
    {"line_range": [True, 6]}, {"line_range": [1]}, {"record_ids": [], "line_range": None},
    {"record_ids": ["P001", "P001"]},
])
def test_source_requires_hash_and_valid_location(payload, change):
    source = payload["evidence"]["metrics"][0]["source_refs"][0]
    with pytest.raises(ValidationError):
        parse(SourceRef, {**source, **change})


@pytest.mark.parametrize("value", [True, 60.0, "NaN", "Infinity", "-1", "101", "1e100000"])
def test_metric_rejects_inexact_nonfinite_and_out_of_range_values(payload, value):
    metric = payload["evidence"]["metrics"][0]
    with pytest.raises(ValidationError):
        parse(Metric, {**metric, "raw_value": value})


def test_decimal_rounding_uses_half_up_and_preserves_exact_raw(payload):
    metric = payload["evidence"]["metrics"][0]
    actual = parse(Metric, {**metric, "raw_value": "12.345", "display_value": "12.35"})
    assert actual.raw_value == Decimal("12.345")
    with pytest.raises(ValidationError):
        parse(Metric, {**metric, "raw_value": "12.345", "display_value": "12.34"})
    assert Metric.model_validate(actual.model_dump()).raw_value == Decimal("12.345")
    with pytest.raises(ValidationError):
        Metric.model_validate({**actual.model_dump(), "raw_value": "12.345"})


def test_display_rounding_does_not_depend_on_callers_decimal_precision(payload):
    metric = payload["evidence"]["metrics"][0]
    with localcontext() as context:
        context.prec = 3
        actual = parse(Metric, {**metric, "raw_value": "12.345", "display_value": "12.35"})
    assert actual.display_value == "12.35"


@pytest.mark.parametrize("change", [
    {"display_value": "60%"}, {"display_value": "59.99"}, {"unit": "个"},
    {"source_refs": []}, {"formula": " "},
])
def test_metric_display_units_and_provenance_are_required(payload, change):
    with pytest.raises(ValidationError):
        parse(Metric, {**payload["evidence"]["metrics"][0], **change})


def test_counts_are_integral_and_zero_is_not_missing(payload):
    metric = {**payload["evidence"]["metrics"][0], "metric_key": "project_count",
              "raw_value": 0, "display_value": "0", "unit": "个", "formula": "count(project_id)"}
    assert parse(Metric, metric).raw_value == 0
    for change in ({"raw_value": "0.5"}, {"unit": "条"}):
        with pytest.raises(ValidationError):
            parse(Metric, {**metric, **change})


def test_evidence_unique_ids_keys_and_consistent_missingness(payload):
    evidence = payload["evidence"]
    metric = evidence["metrics"][0]
    variants = [
        {**evidence, "metrics": [metric, metric]},
        {**evidence, "metrics": [metric, {**metric, "metric_id": "M002"}]},
        {**evidence, "missing_required": ["project_completion_rate"]},
    ]
    for value in variants:
        with pytest.raises(ValidationError):
            parse(EvidenceBundle, value)


def test_missing_metric_is_explicit_and_cannot_enter_writer(payload):
    plan = parse(ReportPlan, payload["plan"])
    missing = parse(EvidenceBundle, {**payload["evidence"], "metrics": [], "missing_required": ["project_completion_rate"]})
    validate_evidence(plan, missing)
    assert not missing.ready_for_writer
    with pytest.raises(ContractError, match="needs input"):
        validate_draft(parse(ReportDraft, payload["draft"]), plan, missing,
                       plan_ref="artifacts/plan-r0.json", evidence_ref="artifacts/evidence-r0.json")
    silent = parse(EvidenceBundle, {**payload["evidence"], "metrics": []})
    with pytest.raises(ContractError, match="neither present nor marked missing"):
        validate_evidence(plan, silent)


@pytest.mark.parametrize("change", [
    {"claims": []},
    {"sections": [{"section_id": "progress", "content": "正文", "claim_ids": []}]},
    {"sections": [{"section_id": "other", "content": "正文", "claim_ids": ["C001"]}]},
    {"plan_ref": "../plan.json"},
])
def test_draft_cannot_contain_dangling_claims_or_unsafe_refs(payload, change):
    with pytest.raises(ValidationError):
        parse(ReportDraft, {**payload["draft"], **change})


@pytest.mark.parametrize("change", [{"value": "75"}, {"unit": "个"}, {"metric_id": "M999"}])
def test_claims_must_match_evidence(payload, change):
    payload["draft"]["claims"][0].update(change)
    with pytest.raises(ContractError, match="differs from supplied evidence"):
        check_draft(objects(payload))


@pytest.mark.parametrize("change", [{"plan_ref": "artifacts/plan-r1.json"}, {"evidence_ref": "artifacts/evidence-r1.json"}])
def test_draft_cannot_reuse_wrong_artifact_version(payload, change):
    payload["draft"].update(change)
    with pytest.raises(ContractError, match="supplied plan and evidence"):
        check_draft(objects(payload))


def test_draft_needs_required_sections_and_metric_claims(payload):
    payload["plan"]["sections"].append({"section_id": "next", "title": "下一步计划", "purpose": "列出后续行动",
                                           "required": True, "required_metrics": []})
    with pytest.raises(ContractError, match="omits a required section"):
        check_draft(objects(payload))
    payload["plan"]["sections"].pop()
    payload["draft"]["claims"] = []
    payload["draft"]["sections"][0]["claim_ids"] = []
    with pytest.raises(ContractError, match="omits required metric claims"):
        check_draft(objects(payload))


def test_review_can_warn_but_cannot_approve_blockers(payload):
    review = payload["review"]
    warning = issue(severity="warning", category="content")
    assert parse(ReviewResult, {**review, "issues": [warning]}).repair_target == "none"
    for value in ({**review, "issues": [issue()]}, {**review, "suggested_repair_target": "writer"},
                  {**review, "decision": "changes_required"},
                  {**review, "decision": "changes_required", "issues": [issue()]}):
        with pytest.raises(ValidationError):
            parse(ReviewResult, value)


@pytest.mark.parametrize("category", ["plan", "data", "required_section", "metric", "source", "version"])
def test_critical_review_errors_cannot_be_downgraded(category):
    with pytest.raises(ValidationError):
        parse(ReviewIssue, issue(category=category, severity="warning"))


@pytest.mark.parametrize("categories,target", [
    (["metric"], "writer"), (["source", "metric"], "data"),
    (["data", "plan", "metric"], "planner"),
])
def test_repair_priority_is_computed_from_issues_not_model_suggestion(payload, categories, target):
    review = parse(ReviewResult, {**payload["review"], "decision": "changes_required",
        "suggested_repair_target": "writer", "issues": [issue(issue_id=f"I{i}", category=category)
                                                         for i, category in enumerate(categories)]})
    assert review.repair_target == target


@pytest.mark.parametrize("change", [
    {"draft_sha256": "d" * 64}, {"draft_ref": "artifacts/draft-r1.json"},
    {"evidence_ref": "artifacts/evidence-r1.json"},
])
def test_review_cannot_approve_stale_artifacts(payload, change):
    payload["review"].update(change)
    with pytest.raises(ContractError, match="current draft hash"):
        check_review(objects(payload))


@pytest.mark.parametrize("change", [{"claim_id": "C999"}, {"section_id": "unknown"}])
def test_review_issues_must_point_to_known_content(payload, change):
    payload["review"].update(decision="changes_required", suggested_repair_target="writer", issues=[issue(**change)])
    with pytest.raises(ContractError):
        check_review(objects(payload))


def test_missing_required_section_can_be_identified_by_planned_id(payload):
    payload["plan"]["sections"].append({"section_id": "next", "title": "下一步计划", "purpose": "列出后续行动",
                                           "required": True, "required_metrics": []})
    payload["review"].update(decision="changes_required", suggested_repair_target="writer",
        issues=[issue(category="required_section", section_id="next", claim_id=None)])
    check_review(objects(payload))
