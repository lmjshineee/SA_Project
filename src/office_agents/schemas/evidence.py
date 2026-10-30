"""Data results with exact decimal values and traceable source locations."""

from decimal import Decimal, ROUND_HALF_UP, localcontext

from pydantic import Field, model_validator

from .base import Contract, ExactDecimal, Identifier, InputPath, Sha256, Text, unique
from .task import MetricKey


class SourceRef(Contract):
    file_id: Identifier
    snapshot_path: InputPath
    sha256: Sha256
    record_ids: list[Identifier]
    line_range: list[int] | None

    @model_validator(mode="after")
    def located(self):
        unique(self.record_ids, "record_ids")
        if self.line_range is not None:
            if (len(self.line_range) != 2 or self.line_range[0] < 1
                    or self.line_range[1] < self.line_range[0]):
                raise ValueError("line_range must be inclusive [start, end], starting at line 1")
        if not self.record_ids and self.line_range is None:
            raise ValueError("source needs record_ids or line_range")
        return self


class Metric(Contract):
    metric_id: Identifier
    metric_key: MetricKey
    raw_value: ExactDecimal = Field(ge=0, max_digits=28)
    display_value: Text
    unit: Text
    formula: Text
    source_refs: list[SourceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_display(self):
        if self.metric_key.endswith("_rate"):
            if self.raw_value > 100 or self.unit != "%":
                raise ValueError("percentage metrics must be in [0, 100] with unit %")
            with localcontext() as context:
                context.prec = 28
                expected = format(self.raw_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")
        else:
            if self.raw_value != self.raw_value.to_integral_value():
                raise ValueError("count metrics must be integral")
            expected = str(int(self.raw_value))
            expected_unit = "条" if self.metric_key in {"feedback_count", "satisfied_feedback_count"} else "个"
            if self.unit != expected_unit:
                raise ValueError(f"count metric unit must be {expected_unit}")
        if self.display_value != expected:
            raise ValueError("display_value must match the exact value and rounding rule, without unit")
        return self


class Excerpt(Contract):
    excerpt_id: Identifier
    text: Text
    source_ref: SourceRef


class EvidenceBundle(Contract):
    metrics: list[Metric]
    excerpts: list[Excerpt]
    missing_required: list[Text]
    limitations: list[Text]

    @model_validator(mode="after")
    def unique_evidence(self):
        unique([item.metric_id for item in self.metrics], "metric_id")
        unique([item.metric_key for item in self.metrics], "metric_key")
        unique([item.excerpt_id for item in self.excerpts], "excerpt_id")
        unique(self.missing_required, "missing_required")
        if set(self.missing_required) & {item.metric_key for item in self.metrics}:
            raise ValueError("a metric cannot be both present and missing")
        return self

    @property
    def ready_for_writer(self) -> bool:
        return not self.missing_required
