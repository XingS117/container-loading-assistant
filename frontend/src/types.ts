export type Orientation = "LWH" | "LHW" | "WLH" | "WHL" | "HLW" | "HWL";
export type CargoKind = "carton" | "pallet";
export type OrientationMode = "upright" | "side" | "any";
export type SolutionProfile = "high_fill" | "stable" | "easy" | "strict_support" | "interstack";

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
  identical_to: SolutionProfile | null;
}

export interface PackResponse {
  request_id: string;
  solutions: PackingSolution[];
}
