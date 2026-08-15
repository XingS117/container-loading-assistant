import type {
  CargoInput,
  ContainerSpec,
  OptimizationGoal,
  PackingSolution,
  PackResponse,
} from "../types";

/** 标准 20GP 柜型（可按需覆盖字段） */
export function makeContainer(overrides: Partial<ContainerSpec> = {}): ContainerSpec {
  return {
    id: "20gp",
    name: "20GP",
    inner_length_mm: 5898,
    inner_width_mm: 2352,
    inner_height_mm: 2393,
    door_width_mm: 2340,
    door_height_mm: 2280,
    max_payload_g: 28200000,
    clearance_mm: 0,
    ...overrides,
  };
}

export function makeCargoItem(overrides: Partial<CargoInput> = {}): CargoInput {
  return {
    id: "cargo-1",
    sku: "SKU-001",
    name: "标准箱",
    kind: "carton",
    length_cm: 60,
    width_cm: 40,
    height_cm: 35,
    weight_kg: 20,
    quantity: 10,
    orientation_mode: "upright",
    stackable: true,
    max_layers: 5,
    max_top_load_kg: 200,
    fragile: false,
    must_load: false,
    unload_order: 0,
    ...overrides,
  };
}

const GOAL_NAMES: Record<OptimizationGoal, string> = {
  high_fill: "装载率优先",
  stable: "重心稳妥",
  easy: "易操作",
};

export function makeSolution(
  profile: OptimizationGoal,
  overrides: Partial<PackingSolution> = {},
): PackingSolution {
  return {
    profile,
    name: GOAL_NAMES[profile],
    placements: [],
    loaded_counts: {},
    unloaded_counts: {},
    metrics: {
      loaded_pieces: 0,
      loaded_weight_g: 0,
      volume_utilization_pct: 0,
      weight_utilization_pct: 0,
      center_of_gravity: { x_mm: 0, y_mm: 0, z_mm: 0 },
      length_imbalance_pct: 0,
      width_imbalance_pct: 0,
      weight_imbalance_pct: 0,
      loading_steps: 0,
      cargo_zones: 0,
    },
    zones: [],
    pros: [],
    cons: [],
    warnings: [],
    layout_fingerprint: "0123456789ab",
    ...overrides,
  };
}

/** 单方案响应：一次计算只返回当前目标的方案 */
export function makeResponse(
  profile: OptimizationGoal = "high_fill",
  overrides: Partial<PackResponse> = {},
): PackResponse {
  return {
    request_id: "test-1",
    solutions: [makeSolution(profile)],
    ...overrides,
  };
}
