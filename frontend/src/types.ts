export type Orientation = "LWH" | "LHW" | "WLH" | "WHL" | "HLW" | "HWL";
export type CargoKind = "carton" | "pallet";
export type OrientationMode = "upright" | "side" | "any";
export type SolutionProfile = "high_fill" | "stable" | "easy";
/** 优化目标偏好：与后端 PackRequest.optimization_goal 对应，切换即重新计算 */
export type OptimizationGoal = SolutionProfile;

export interface ContainerSpec {
  id: string;
  name: string;
  inner_length_mm: number;
  inner_width_mm: number;
  inner_height_mm: number;
  door_width_mm: number;
  door_height_mm: number;
  max_payload_g: number;
  clearance_mm: number;
}

export interface CargoInput {
  id: string;
  sku: string;
  name: string;
  kind: CargoKind;
  length_cm: number;
  width_cm: number;
  height_cm: number;
  weight_kg: number;
  quantity: number;
  orientation_mode: OrientationMode;
  stackable: boolean;
  max_layers: number;
  max_top_load_kg: number;
  fragile: boolean;
  must_load: boolean;
  unload_order: number;
}

export interface Placement {
  id: string;
  cargo_id: string;
  instance_index: number;
  x_mm: number;
  y_mm: number;
  z_mm: number;
  length_mm: number;
  width_mm: number;
  height_mm: number;
  rotation: Orientation;
  weight_g: number;
  step: number;
}

export interface SolutionMetrics {
  loaded_pieces: number;
  loaded_weight_g: number;
  volume_utilization_pct: number;
  weight_utilization_pct: number;
  center_of_gravity: { x_mm: number; y_mm: number; z_mm: number };
  length_imbalance_pct: number;
  width_imbalance_pct: number;
  weight_imbalance_pct: number;
  loading_steps: number;
  cargo_zones: number;
}

export interface Zone {
  step: number;
  cargo_id: string;
  x_mm: number;
  y_mm: number;
  length_mm: number;
  width_mm: number;
  piece_count: number;
}

export interface PackingSolution {
  profile: SolutionProfile;
  name: string;
  placements: Placement[];
  loaded_counts: Record<string, number>;
  unloaded_counts: Record<string, number>;
  metrics: SolutionMetrics;
  zones: Zone[];
  pros: string[];
  cons: string[];
  warnings: string[];
  /** 平移归一几何指纹：切换目标后与上一方案对比，相同则披露"布局几何相同" */
  layout_fingerprint?: string;
}

export interface PackResponse {
  request_id: string;
  solutions: PackingSolution[];
}
