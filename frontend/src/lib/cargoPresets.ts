import type { CargoInput, CargoPreset } from "../types";

type PresetItem = Omit<CargoInput, "id">;

const pallet = (
  sku: string,
  name: string,
  length_cm: number,
  width_cm: number,
  height_cm: number,
  quantity: number,
  weight_kg: number | null,
  stackable: boolean,
  max_layers = stackable ? 2 : 1,
): PresetItem => ({
  sku,
  name,
  kind: "pallet",
  length_cm,
  width_cm,
  height_cm,
  weight_kg,
  quantity,
  orientation_mode: "upright",
  stackable,
  max_layers,
  max_top_load_kg: 500,
  fragile: false,
  must_load: false,
  unload_order: 0,
});

const abcItems = [
  pallet("ZT1", "ZT1 常见整托", 65, 65, 120, 30, 150, true),
  pallet("ZT2", "ZT2 常见整托", 89, 75, 112, 30, 280, true),
  pallet("ZT3", "ZT3 常见整托", 108, 80, 125, 3, 400, false),
];

const fourSkuItems = [
  pallet("ZT1", "ZT1 常见整托", 76, 76, 100, 18, null, true),
  pallet("ZT2", "ZT2 常见整托", 115, 115, 110, 8, null, true),
  pallet("ZT3", "ZT3 常见整托", 110, 110, 100, 12, null, true),
  pallet("ZT4", "ZT4 常见整托", 105, 105, 80, 12, null, true),
];

const fiveSkuItems = [
  pallet("ZT1", "ZT1 常见整托", 70, 70, 110, 22, null, true),
  pallet("ZT2", "ZT2 常见整托", 90, 75, 108, 25, null, true),
  pallet("ZT3", "ZT3 常见整托", 108, 80, 105, 5, null, true),
  pallet("ZT4", "ZT4 常见整托", 100, 80, 100, 1, null, false),
  pallet("ZT5", "ZT5 常见整托", 122, 92, 118, 2, null, false),
];

const combination = (
  id: string,
  label: string,
  group: string,
  items: PresetItem[],
  description: string,
): CargoPreset => ({
  id,
  label,
  kind: "组合",
  group,
  containerHint: "40HQ",
  description,
  items,
});

const single = (
  id: string,
  item: PresetItem,
  group: string,
): CargoPreset => ({
  id,
  label: `${item.sku} · ${item.length_cm} × ${item.width_cm} × ${item.height_cm} cm`,
  kind: "单品",
  group,
  containerHint: "40HQ",
  description: "已预填基本尺寸；数量需自行填写，其他参数可按实际情况修改。",
  items: [{ ...item, quantity: 0 }],
});

let presetSequence = 1;

function nextPresetSequence(): number {
  return presetSequence++;
}

export const COMMON_CARGO_PRESETS: CargoPreset[] = [
  combination(
    "common-abc",
    "三 SKU 案例（3 种整托，63 托）",
    "核心组合案例",
    abcItems,
    "3 种整托 · ZT1 30 + ZT2 30 + ZT3 3；底层按客户常用分带，柜门端放不可叠货物，上层按同规格集中。",
  ),
  combination(
    "common-case-4",
    "四 SKU 案例（4 种整托，50 托）",
    "核心组合案例",
    fourSkuItems,
    "4 种整托 · 76×76×100、115×115×110、110×110×100、105×105×80；按客户常用排组预置。",
  ),
  combination(
    "common-case-5",
    "五 SKU 案例（5 种整托，55 托）",
    "核心组合案例",
    fiveSkuItems,
    "5 种整托 · 共 55 托；主体先成排，余托按尺寸横放或纵放组合。",
  ),
  ...[
    ...abcItems.map((item, index) => single(`common-single-abc-${index}`, item, "常用单品规格")),
    ...fourSkuItems.map((item, index) => single(`common-single-4-${index}`, item, "常用单品规格")),
    ...fiveSkuItems.map((item, index) => single(`common-single-5-${index}`, item, "常用单品规格")),
  ],
];

export function cloneCargoPreset(preset: CargoPreset): CargoInput[] {
  return preset.items.map((item) => ({
    ...item,
    id: `cargo_${Date.now()}_${nextPresetSequence()}`,
  }));
}
