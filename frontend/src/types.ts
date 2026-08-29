export type Orientation = "LWH" | "LHW" | "WLH" | "WHL" | "HLW" | "HWL";
export type CargoKind = "carton" | "pallet";
export type OrientationMode = "upright" | "side" | "any";
export type SolutionProfile = "high_fill" | "stable" | "easy";
export type AIProvider = "deepseek" | "qwen" | "zhipu";

export interface AIModelConfig {
  provider: AIProvider;
  model: string;
  baseUrl: string;
  apiKey: string;
}

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
  weight_kg: number | null;
  quantity: number;
  orientation_mode: OrientationMode;
  stackable: boolean;
  max_layers: number;
  max_top_load_kg: number;
  fragile: boolean;
  must_load: boolean;
  unload_order: number;
}

export interface CargoPreset {
  id: string;
  label: string;
  kind: "组合" | "单品";
  group: string;
  containerHint: string;
  description: string;
  items: Array<Omit<CargoInput, "id">>;
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
  floor_internal_gap_mm?: number;
  floor_largest_gap_mm?: number;
  floor_bbox_void_pct?: number;
  floor_largest_transverse_gap_mm?: number;
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
  identical_to: SolutionProfile | null;
}

export interface AIStrategyStatus {
  status: "disabled" | "fallback" | "considered";
  applied: boolean;
  provider: string | null;
  model: string | null;
  message: string;
  sku_order: string[];
  orientations: Record<string, string>;
  row_groups: string[][];
  coordinate_candidates_applied?: SolutionProfile[];
  profiles?: Partial<Record<SolutionProfile, {
    sku_order?: string[];
    orientations?: Record<string, string>;
    row_groups?: string[][];
    zone_order?: string[];
    max_zones?: number;
  }>>;
}

export interface PackResponse {
  request_id: string;
  solutions: PackingSolution[];
  ai_strategy?: AIStrategyStatus;
}
