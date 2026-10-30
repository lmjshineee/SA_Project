"""Manager and Planner output contracts."""

from typing import Literal

from pydantic import Field, model_validator

from .base import Contract, Identifier, Period, Text, unique

MetricKey = Literal[
    "project_count", "completed_project_count", "project_completion_rate",
    "attendance_rate", "achievement_count", "satisfied_feedback_count",
    "feedback_count", "satisfaction_rate",
]
MissingField = Literal["title", "department", "period", "required_sections", "input_file_ids"]


class TaskSpec(Contract):
    title: Text | None
    department: Text | None
    period: Period | None
    required_sections: list[Text]
    output_format: Literal["markdown"]
    input_file_ids: list[Identifier]
    missing_fields: list[MissingField]

    @model_validator(mode="after")
    def consistent_missing_fields(self):
        for field in ("required_sections", "input_file_ids", "missing_fields"):
            unique(getattr(self, field), field)
        missing = {field for field in ("title", "department", "period", "required_sections", "input_file_ids")
                   if not getattr(self, field)}
        if set(self.missing_fields) != missing:
            raise ValueError("missing_fields must exactly identify absent required task fields")
        return self

    @property
    def ready_for_planner(self) -> bool:
        return not self.missing_fields


class PlanSection(Contract):
    section_id: Identifier
    title: Text
    purpose: Text
    required: bool
    required_metrics: list[MetricKey]

    @model_validator(mode="after")
    def unique_metrics(self):
        unique(self.required_metrics, "required_metrics")
        return self


class ReportPlan(Contract):
    sections: list[PlanSection] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sections(self):
        unique([item.section_id for item in self.sections], "section_id")
        unique([item.title for item in self.sections], "section title")
        return self
