import { orientationsFor } from "./cargo";
import type { CargoInput, ContainerSpec, PackResponse } from "../types";

export async function getContainerPresets(): Promise<ContainerSpec[]> {
  const response = await fetch("/api/v1/container-presets");
  if (!response.ok) throw new Error("无法读取标准柜型，请稍后重试");
  return response.json();
}

export async function packOrder(
  container: ContainerSpec,
  cargoItems: CargoInput[],
  itemGapCm: number,
): Promise<PackResponse> {
  const response = await fetch("/api/v1/pack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      container,
      item_gap_mm: Math.round(itemGapCm * 10),
      cargo_items: cargoItems.map((item) => ({
        id: item.id,
        sku: item.sku.trim(),
        name: item.name.trim(),
        kind: item.kind,
        length_mm: Math.round(item.length_cm * 10),
        width_mm: Math.round(item.width_cm * 10),
        height_mm: Math.round(item.height_cm * 10),
        weight_g: Math.round(item.weight_kg * 1000),
        quantity: item.quantity,
        allowed_orientations: orientationsFor(item.orientation_mode),
        stackable: item.stackable,
        max_layers: item.stackable ? item.max_layers : 1,
        max_top_load_g: item.stackable ? Math.round(item.max_top_load_kg * 1000) : 0,
        fragile: item.fragile,
        must_load: item.must_load,
      })),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.error?.message ?? "计算失败，请检查货物参数";
    const hint = payload?.error?.hint as string | undefined;
    throw new Error(hint ? `${message}\n${hint}` : message);
  }
  return payload;
}

