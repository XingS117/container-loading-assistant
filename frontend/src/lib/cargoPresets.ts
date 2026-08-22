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
  pallet("A", "A 常见整托", 65, 65, 120, 30, 150, true),
  pallet("B", "B 常见整托", 89, 75, 112, 30, 280, true),
  pallet("C", "C 常见整托", 108, 80, 125, 3, 400, false),
];

const fourSkuItems = [
  pallet("P1", "76 × 76 × 100 常见整托", 76, 76, 100, 18, null, true),
  pallet("P2", "115 × 115 × 110 常见整托", 115, 115, 110, 8, null, true),
  pallet("P3", "110 × 110 × 100 常见整托", 110, 110, 100, 12, null, true),
  pallet("P4", "105 × 105 × 80 常见整托", 105, 105, 80, 12, null, true),
];

const fiveSkuItems = [
  pallet("Q1", "70 × 70 × 110 常见整托", 70, 70, 110, 22, null, true),
  pallet("Q2", "90 × 75 × 108 常见整托", 90, 75, 108, 25, null, true),
  pallet("Q3", "108 × 80 × 105 常见整托", 108, 80, 105, 5, null, true),
  pallet("Q4", "100 × 80 × 100 常见整托", 100, 80, 100, 1, null, false),
  pallet("Q5", "122 × 92 × 118 常见整托", 122, 92, 118, 2, null, false),
];

const combination = (
  id: string,
  label: string,
  items: PresetItem[],
  description: string,
): CargoPreset => ({
  id,
  label,
  kind: "组合",
  containerHint: "40HQ",
  description,
  items,
});

const single = (
  id: string,
  item: PresetItem,
  source: string,
): CargoPreset => ({
  id,
  label: `${item.name}（${source}）`,
  kind: "单品",
  containerHint: "40HQ",
  description: "常见整托规格，可加载后继续修改数量、重量和叠放参数。",
  items: [item],
});

let presetSequence = 1;

function nextPresetSequence(): number {
  return presetSequence++;
}

export const COMMON_CARGO_PRESETS: CargoPreset[] = [
  combination(
    "common-abc",
    "三 SKU 案例",
    abcItems,
    "B、A、B 底层分带，C 靠柜门底层，A/B 剩余货物集中到中部同规格支撑上。",
  ),
  combination(
    "common-case-4",
    "四 SKU 案例（4 种整托）",
    fourSkuItems,
    "76、105、115、110 规格分别形成连续整排，均按客户常用的两层方式预置。",
  ),
  combination(
    "common-case-5",
    "五 SKU 案例（5 种整托）",
    fiveSkuItems,
    "主体货物先成排，余托按 108+100、90+122 横放、70+122 纵放组成柜门端混合排。",
  ),
  ...[
    ...fourSkuItems.map((item, index) => single(`common-single-4-${index}`, item, "四 SKU 案例")),
    ...fiveSkuItems.map((item, index) => single(`common-single-5-${index}`, item, "五 SKU 案例")),
    ...abcItems.map((item, index) => single(`common-single-abc-${index}`, item, "三 SKU 案例")),
  ],
];

export function cloneCargoPreset(preset: CargoPreset): CargoInput[] {
  return preset.items.map((item) => ({
    ...item,
    id: `cargo_${Date.now()}_${nextPresetSequence()}`,
  }));
}
