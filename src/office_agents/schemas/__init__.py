"""Validated business payloads for the five planned roles (schema version 1.0)."""

from .evidence import EvidenceBundle, Excerpt, Metric, SourceRef
from .report import DraftSection, FactClaim, ReportDraft, ReviewIssue, ReviewResult
from .task import PlanSection, ReportPlan, TaskSpec
from .validation import ContractError, validate_draft, validate_evidence, validate_plan, validate_review

__all__ = [
    "TaskSpec", "PlanSection", "ReportPlan", "SourceRef", "Metric", "Excerpt",
    "EvidenceBundle", "DraftSection", "FactClaim", "ReportDraft", "ReviewIssue", "ReviewResult",
    "ContractError", "validate_plan", "validate_evidence", "validate_draft", "validate_review",
]
