from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Orientation(StrEnum):
    LWH = "LWH"
    LHW = "LHW"
    WLH = "WLH"
    WHL = "WHL"
    HLW = "HLW"
    HWL = "HWL"


class ContainerSpec(BaseModel):
    id: str
    name: str
    inner_length_mm: int = Field(gt=0)
    inner_width_mm: int = Field(gt=0)
    inner_height_mm: int = Field(gt=0)
    door_width_mm: int = Field(gt=0)
    door_height_mm: int = Field(gt=0)
    max_payload_g: int = Field(gt=0)
    clearance_mm: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_clearance(self) -> "ContainerSpec":
        if self.clearance_mm * 2 >= min(
            self.inner_length_mm,
            self.inner_width_mm,
            self.inner_height_mm,
        ):
            raise ValueError("安全边距不能占满柜体")
        return self


class CargoSpec(BaseModel):
    id: str
    sku: str
    name: str
    kind: Literal["carton", "pallet"]
    length_mm: int = Field(gt=0)
    width_mm: int = Field(gt=0)
    height_mm: int = Field(gt=0)
    weight_g: int = Field(gt=0)
    quantity: int = Field(ge=1, le=5000)
    allowed_orientations: list[Orientation] = Field(min_length=1)
    stackable: bool = True
    max_layers: int = Field(default=1, ge=1, le=100)
    max_top_load_g: int = Field(default=0, ge=0)
    fragile: bool = False
    must_load: bool = False
    # 卸货顺序：0 = 不指定；>0 时数字小者先卸。布局按"后卸先装"排布
    # （卸货顺序大的货物先装进柜头，先卸的靠柜门）。
    unload_order: int = Field(default=0, ge=0)

    def dimensions_for(self, orientation: Orientation | str) -> tuple[int, int, int]:
        values = {
            "L": self.length_mm,
            "W": self.width_mm,
            "H": self.height_mm,
        }
        key = Orientation(orientation).value
        return values[key[0]], values[key[1]], values[key[2]]


class Placement(BaseModel):
    id: str
    cargo_id: str
    instance_index: int = Field(ge=0)
    x_mm: int = Field(ge=0)
    y_mm: int = Field(ge=0)
    z_mm: int = Field(ge=0)
    length_mm: int = Field(gt=0)
    width_mm: int = Field(gt=0)
    height_mm: int = Field(gt=0)
    rotation: Orientation
    weight_g: int = Field(gt=0)
    step: int = Field(ge=1)


class ValidationIssue(BaseModel):
    code: str
    message: str
    placement_ids: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationIssue]


class PackRequest(BaseModel):
    container: ContainerSpec
    cargo_items: list[CargoSpec] = Field(min_length=1, max_length=30)
    item_gap_mm: int = Field(default=0, ge=0, le=1000)
    # 门端操作空间：可用柜长 = 柜长 - door_buffer_mm（默认 300，0=关闭）
    door_buffer_mm: int = Field(default=300, ge=0)
    # 旧版互叠参数保留用于兼容请求解析；正式方案始终使用完整支撑约束。
    enable_interstack: bool = Field(default=True)
    # 旧版互叠参数，仅用于兼容请求解析，不改变正式方案安全约束。
    support_coverage_min: float = Field(default=0.7, ge=0.0, le=1.0)
    overhang_ratio_max: float = Field(default=0.2, ge=0.0, le=0.5)
    # Internal, optional AI strategy hint. It is never trusted for physical validation.
    ai_layout_hint: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_total_quantity(self) -> "PackRequest":
        if sum(item.quantity for item in self.cargo_items) > 5000:
            raise ValueError("单次计算最多支持 5000 件货物")
        if len({item.id for item in self.cargo_items}) != len(self.cargo_items):
            raise ValueError("货物 ID 不能重复")
        return self


class CenterOfGravity(BaseModel):
    x_mm: float
    y_mm: float
    z_mm: float


class SolutionMetrics(BaseModel):
    loaded_pieces: int
    loaded_weight_g: int
    volume_utilization_pct: float
    weight_utilization_pct: float
    center_of_gravity: CenterOfGravity
    length_imbalance_pct: float
    width_imbalance_pct: float
    weight_imbalance_pct: float
    loading_steps: int
    cargo_zones: int


class Zone(BaseModel):
    step: int = Field(ge=1)
    cargo_id: str
    x_mm: int = Field(ge=0)
    y_mm: int = Field(ge=0)
    length_mm: int = Field(gt=0)
    width_mm: int = Field(gt=0)
    piece_count: int = Field(ge=1)


class PackingSolution(BaseModel):
    profile: Literal["high_fill", "stable", "easy"]
    name: str
    placements: list[Placement]
    loaded_counts: dict[str, int]
    unloaded_counts: dict[str, int]
    metrics: SolutionMetrics
    zones: list[Zone] = Field(default_factory=list)
    pros: list[str]
    cons: list[str]
    warnings: list[str] = Field(default_factory=list)
    identical_to: str | None = None


class AIStrategyStatus(BaseModel):
    status: Literal["disabled", "fallback", "considered"]
    applied: bool = False
    provider: str | None = None
    model: str | None = None
    message: str
    sku_order: list[str] = Field(default_factory=list)
    orientations: dict[str, str] = Field(default_factory=dict)
    row_groups: list[list[str]] = Field(default_factory=list)


class PackResponse(BaseModel):
    request_id: str
    solutions: list[PackingSolution]
    ai_strategy: AIStrategyStatus = Field(
        default_factory=lambda: AIStrategyStatus(
            status="disabled",
            message="未启用 AI 策略，当前使用本地装柜算法",
        )
    )
