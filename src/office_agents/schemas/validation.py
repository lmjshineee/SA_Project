"""Pure cross-object checks. These functions do not read files or call models."""

from .evidence import EvidenceBundle
from .report import ReportDraft, ReviewResult
from .task import ReportPlan, TaskSpec


class ContractError(ValueError):
    """Objects are individually valid but do not form a consistent handoff."""


def validate_plan(task: TaskSpec, plan: ReportPlan) -> None:
    if not task.ready_for_planner:
        raise ContractError("task needs input before planning")
    sections = {section.title: section for section in plan.sections}
    for title in task.required_sections:
        if title not in sections or not sections[title].required:
            raise ContractError(f"plan must preserve required section: {title}")


def validate_evidence(plan: ReportPlan, evidence: EvidenceBundle) -> None:
    """Absent required metrics must be explicit; incomplete evidence stays valid."""
    present = {metric.metric_key for metric in evidence.metrics}
    for section in plan.sections:
        if section.required:
            for key in section.required_metrics:
                if key not in present and key not in evidence.missing_required:
                    raise ContractError(f"required metric is neither present nor marked missing: {key}")


def validate_draft(
    draft: ReportDraft, plan: ReportPlan, evidence: EvidenceBundle,
    *, plan_ref: str, evidence_ref: str,
) -> None:
    validate_evidence(plan, evidence)
    if not evidence.ready_for_writer:
        raise ContractError("evidence needs input before writing")
    if draft.plan_ref != plan_ref or draft.evidence_ref != evidence_ref:
        raise ContractError("draft must reference the supplied plan and evidence artifacts")
    plan_sections = {section.section_id: section for section in plan.sections}
    draft_sections = {section.section_id: section for section in draft.sections}
    if any(section_id not in plan_sections for section_id in draft_sections):
        raise ContractError("draft contains an unplanned section")
    if any(section.required and section.section_id not in draft_sections for section in plan.sections):
        raise ContractError("draft omits a required section")
    metrics = {metric.metric_id: metric for metric in evidence.metrics}
    for claim in draft.claims:
        metric = metrics.get(claim.metric_id)
        if metric is None or claim.value != metric.raw_value or claim.unit != metric.unit:
            raise ContractError(f"claim differs from supplied evidence: {claim.claim_id}")
    for section_id in draft_sections:
        cited = {metrics[claim.metric_id].metric_key for claim in draft.claims if claim.section_id == section_id}
        if not set(plan_sections[section_id].required_metrics) <= cited:
            raise ContractError(f"section omits required metric claims: {section_id}")


def validate_review(
    review: ReviewResult, draft: ReportDraft, plan: ReportPlan,
    *, draft_ref: str, draft_sha256: str, evidence_ref: str,
) -> None:
    """Compare against trusted artifact metadata supplied by the future service."""
    if (review.draft_ref != draft_ref or review.draft_sha256 != draft_sha256
            or review.evidence_ref != evidence_ref or draft.evidence_ref != evidence_ref):
        raise ContractError("review must match the current draft hash and evidence reference")
    sections = {section.section_id for section in draft.sections}
    planned = {section.section_id for section in plan.sections}
    claims = {claim.claim_id: claim for claim in draft.claims}
    for issue in review.issues:
        allowed = sections | planned if issue.category in {"plan", "required_section"} else sections
        if issue.section_id is not None and issue.section_id not in allowed:
            raise ContractError("review points to an unknown draft section")
        if issue.claim_id is not None:
            claim = claims.get(issue.claim_id)
            if claim is None or (issue.section_id is not None and issue.section_id != claim.section_id):
                raise ContractError("review claim does not belong to its stated section")
