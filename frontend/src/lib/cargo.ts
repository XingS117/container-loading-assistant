import type { CargoInput, Orientation } from "../types";

let cargoSequence = 1;

export function createCargo(sku?: string): CargoInput {
  const sequence = cargoSequence++;
  return {
    id: `cargo_${Date.now()}_${sequence}`,
    sku: sku ?? `SKU-${String(sequence).padStart(3, "0")}`,
    name: "标准纸箱",
    kind: "carton",
    length_cm: 60,
    width_cm: 40,
    height_cm: 40,
    weight_kg: 18,
    quantity: 10,
    orientation_mode: "upright",
    stackable: true,
    max_layers: 5,
    max_top_load_kg: 90,
    fragile: false,
    must_load: false,
  };
}

export function orientationsFor(mode: CargoInput["orientation_mode"]): Orientation[] {
  if (mode === "upright") return ["LWH", "WLH"];
  if (mode === "side") return ["LWH", "WLH", "LHW", "WHL"];
  return ["LWH", "LHW", "WLH", "WHL", "HLW", "HWL"];
}

export function validateCargo(rows: CargoInput[]): string | null {
  if (!rows.length) return "请至少添加一种货物";
  if (rows.length > 30) return "单次最多支持 30 种货物";
  if (rows.some((row) => !row.sku.trim() || !row.name.trim())) {
    return "SKU 和货物名称不能为空";
  }
  if (
    rows.some(
      (row) =>
        row.length_cm <= 0 ||
        row.width_cm <= 0 ||
        row.height_cm <= 0 ||
        row.weight_kg <= 0 ||
        row.quantity < 1,
    )
  ) {
    return "尺寸、重量和数量必须大于 0";
  }
  if (rows.reduce((sum, row) => sum + row.quantity, 0) > 5000) {
    return "单次计算最多支持 5000 件货物";
  }
  return null;
}

