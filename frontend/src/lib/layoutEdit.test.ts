import { rotateCargoPlacements } from "./layoutEdit";
import type { CargoInput, ContainerSpec, Placement } from "../types";

const container = { inner_length_mm: 2000, inner_width_mm: 1000, inner_height_mm: 1000 } as ContainerSpec;
const cargo = { id: "a", sku: "A", orientation_mode: "any" } as CargoInput;
const placement = { id: "a-0", cargo_id: "a", instance_index: 0, x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 800, width_mm: 400, height_mm: 500, rotation: "LWH", weight_g: 1, step: 1 } as Placement;

test("rotates a selected cargo while keeping its center", () => {
  const result = rotateCargoPlacements([placement], cargo, container);
  expect(result.error).toBeUndefined();
  expect(result.placements[0]).toMatchObject({ length_mm: 400, width_mm: 800, rotation: "WLH" });
});

test("rejects a rotation that causes a collision", () => {
  const other = { ...placement, id: "b-0", cargo_id: "b", x_mm: 300, width_mm: 500 };
  expect(rotateCargoPlacements([placement, other], cargo, container).error).toContain("碰撞");
});
