"""Writer and Checker outputs; repair suggestions do not control routing."""

from typing import Literal

from pydantic import Field, model_validator

from .base import ArtifactPath, Contract, ExactDecimal, Identifier, Sha256, Text, unique

RepairTarget = Literal["writer", "data", "planner", "none"]
IssueCategory = Literal["plan", "data", "required_section", "metric", "source", "version", "content", "format"]
REPAIR_TARGETS = {
    "plan": "planner", "data": "data", "source": "data",
    "required_section": "writer", "metric": "writer", "version": "writer",
    "content": "writer", "format": "writer",
}


class FactClaim(Contract):
    claim_id: Identifier
    section_id: Identifier
    metric_id: Identifier
    value: ExactDecimal = Field(ge=0, max_digits=28)
    unit: Text


class DraftSection(Contract):
    section_id: Identifier
    content: Text
    claim_ids: list[Identifier]


class ReportDraft(Contract):
    sections: list[DraftSection] = Field(min_length=1)
    claims: list[FactClaim]
    plan_ref: ArtifactPath
    evidence_ref: ArtifactPath

    @model_validator(mode="after")
    def coherent_claims(self):
        unique([item.section_id for item in self.sections], "section_id")
        unique([item.claim_id for item in self.claims], "claim_id")
        sections = {item.section_id: item for item in self.sections}
        claims = {item.claim_id: item for item in self.claims}
        for section in self.sections:
            unique(section.claim_ids, "claim_ids")
            for claim_id in section.claim_ids:
                if claim_id not in claims or claims[claim_id].section_id != section.section_id:
                    raise ValueError("section claim_ids must refer to its own existing claims")
        for claim in self.claims:
            if claim.section_id not in sections or claim.claim_id not in sections[claim.section_id].claim_ids:
                raise ValueError("every claim must be listed in its owning section")
        return self


class ReviewIssue(Contract):
    issue_id: Identifier
    severity: Literal["blocker", "warning"]
    category: IssueCategory
    section_id: Identifier | None
    claim_id: Identifier | None
    expected: Text
    actual: Text
    suggestion: Text

    @model_validator(mode="after")
    def critical_issues_block(self):
        if self.category not in {"content", "format"} and self.severity != "blocker":
            raise ValueError("plan, data, required section, metric, source and version errors must block")
        return self


class ReviewResult(Contract):
    decision: Literal["approved", "changes_required"]
    issues: list[ReviewIssue]
    suggested_repair_target: RepairTarget
    draft_ref: ArtifactPath
    draft_sha256: Sha256
    evidence_ref: ArtifactPath

    @model_validator(mode="after")
    def consistent_decision(self):
        unique([item.issue_id for item in self.issues], "issue_id")
        if self.decision == "approved":
            if any(item.severity == "blocker" for item in self.issues) or self.suggested_repair_target != "none":
                raise ValueError("approved reviews require no blockers and repair target none")
        elif not self.issues or self.suggested_repair_target == "none":
            raise ValueError("changes_required needs concrete issues and a repair suggestion")
        return self

    @property
    def repair_target(self) -> RepairTarget:
        if self.decision == "approved":
            return "none"
        targets = {REPAIR_TARGETS[item.category] for item in self.issues}
        return next(target for target in ("planner", "data", "writer") if target in targets)
