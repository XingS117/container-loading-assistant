import { movePlacement, rotateCargoPlacements, swapCargoPlacements } from "./layoutEdit";
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

test("rejects editing a locked cargo", () => {
  expect(rotateCargoPlacements([placement], cargo, container, new Set(["a"])).error).toContain("已锁定");
});

test("swaps matching cargo positions", () => {
  const second = { ...placement, id: "b-0", cargo_id: "b", x_mm: 1000 };
  const result = swapCargoPlacements([placement, second], "a", "b");
  expect(result.error).toBeUndefined();
  expect(result.placements.find((item) => item.cargo_id === "a")?.x_mm).toBe(1000);
});

test("rejects moving a locked placement", () => {
  expect(movePlacement([placement], "a-0", 100, 100, container, new Set(["a"])).error).toContain("锁定");
});
