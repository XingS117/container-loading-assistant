from pydantic import Field, model_validator

from .models import PackRequest, Placement, SolutionMetrics, ValidationResult, Zone
from .packing import _compute_zones, _metrics
from .validator import validate_solution


class LayoutReviewRequest(PackRequest):
    placements: list[Placement] = Field(max_length=5000)

    @model_validator(mode="after")
    def unique_placement_ids(self):
        if len({p.id for p in self.placements}) != len(self.placements):
            raise ValueError("布局中的货物标识不能重复")
        return self


class LayoutReview(ValidationResult):
    metrics: SolutionMetrics | None = None
    zones: list[Zone] = Field(default_factory=list)


def review_layout(request: LayoutReviewRequest) -> LayoutReview:
    validation = validate_solution(
        request.container, request.cargo_items, request.placements,
        item_gap_mm=request.item_gap_mm,
    )
    return LayoutReview(
        **validation.model_dump(),
        metrics=_metrics(request, request.placements) if validation.valid else None,
        zones=_compute_zones(request, request.placements) if validation.valid else [],
    )
