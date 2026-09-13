import type { ContainerSpec, CargoInput, Placement, SolutionMetrics } from "../types";

export function recalculateMetrics(container: ContainerSpec, cargoItems: CargoInput[], placements: Placement[], stepCount: number, zoneCount: number): SolutionMetrics {
  void cargoItems;
  const totalVolume = container.inner_length_mm * container.inner_width_mm * container.inner_height_mm;
  const loadedWeight = placements.reduce((sum, item) => sum + item.weight_g, 0);
  const loadedVolume = placements.reduce((sum, item) => sum + item.length_mm * item.width_mm * item.height_mm, 0);
  const weightByAxis = placements.reduce((sum, item) => ({
    x: sum.x + item.weight_g * (item.x_mm + item.length_mm / 2),
    y: sum.y + item.weight_g * (item.y_mm + item.width_mm / 2),
    z: sum.z + item.weight_g * (item.z_mm + item.height_mm / 2),
  }), { x: 0, y: 0, z: 0 });
  const center = loadedWeight ? { x_mm: weightByAxis.x / loadedWeight, y_mm: weightByAxis.y / loadedWeight, z_mm: weightByAxis.z / loadedWeight } : { x_mm: 0, y_mm: 0, z_mm: 0 };
  const axisImbalance = (value: number, size: number) => loadedWeight ? Math.round(Math.abs(value - size / 2) / (size / 2) * 10000) / 100 : 0;
  return {
    loaded_pieces: placements.length,
    loaded_weight_g: loadedWeight,
    volume_utilization_pct: Math.round(loadedVolume / totalVolume * 10000) / 100,
    weight_utilization_pct: Math.round(loadedWeight / container.max_payload_g * 10000) / 100,
    center_of_gravity: center,
    length_imbalance_pct: axisImbalance(center.x_mm, container.inner_length_mm),
    width_imbalance_pct: axisImbalance(center.y_mm, container.inner_width_mm),
    weight_imbalance_pct: axisImbalance(center.z_mm, container.inner_height_mm),
    loading_steps: stepCount,
    cargo_zones: zoneCount,
  };
}
