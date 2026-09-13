import { recalculateMetrics } from "./layoutMetrics";
import type { CargoInput, ContainerSpec, Placement } from "../types";

const container = { inner_length_mm: 1000, inner_width_mm: 1000, inner_height_mm: 1000, max_payload_g: 1000 } as ContainerSpec;
const cargo = { id: "a" } as CargoInput;
const placement = { id: "a-0", cargo_id: "a", x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 500, width_mm: 500, height_mm: 500, weight_g: 100, step: 1 } as Placement;

test("recalculates edited layout metrics from placements", () => {
  expect(recalculateMetrics(container, [cargo], [placement], 1, 1)).toMatchObject({ loaded_pieces: 1, loaded_weight_g: 100, volume_utilization_pct: 12.5, weight_utilization_pct: 10, length_imbalance_pct: 50 });
});
