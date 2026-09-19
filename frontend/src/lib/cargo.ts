import type { CargoInput, ContainerSpec, Orientation } from "../types";

let cargoSequence = 1;

export function createCargo(sku?: string): CargoInput {
  const sequence = cargoSequence++;
  return {
    id: `cargo_${Date.now()}_${sequence}`,
    sku: sku ?? `SKU-${String(sequence).padStart(3, "0")}`,
    // name remains in the model for old API and Excel data; the UI uses one label field.
    name: sku ?? `SKU-${String(sequence).padStart(3, "0")}`,
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
    unload_order: 0,
  };
}

export function orientationsFor(mode: CargoInput["orientation_mode"]): Orientation[] {
  if (mode === "upright") return ["LWH", "WLH"];
  if (mode === "side") return ["LWH", "WLH", "LHW", "WHL"];
  return ["LWH", "LHW", "WLH", "WHL", "HLW", "HWL"];
}

export function validateCargo(rows: CargoInput[]): string | null {
  if (rows.some((row) => row.weight_kg == null)) {
    return "请先补充所有货物的单托重量";
  }
  const issue = validateCargoIssues(rows)[0];
  return issue ? `${issue.row ? `第 ${issue.row} 种货物：` : ''}${issue.field}${issue.message}` : null;
}

export interface CargoValidationIssue {
  row: number;
  field: string;
  message: string;
}

export function validateCargoIssues(rows: CargoInput[]): CargoValidationIssue[] {
  const issues: CargoValidationIssue[] = [];
  if (!rows.length) issues.push({ row: 0, field: '货物', message: '请至少添加一种货物' });
  rows.forEach((row, index) => {
    const rowNumber = index + 1;
    const add = (field: string, message: string) => issues.push({ row: rowNumber, field, message });
    if (!row.sku.trim()) issues.push({ row: rowNumber, field: "货物代号", message: "不能为空" });
    for (const [field, value, precision] of [['单重', row.weight_kg, 1000], ['长', row.length_cm, 10], ['宽', row.width_cm, 10], ['高', row.height_cm, 10]] as const) {
      if (value == null || !Number.isFinite(value) || value <= 0) add(field, '必须大于 0');
      else if (Math.round(value * precision) < 1 || !Number.isSafeInteger(Math.round(value * precision)) || Math.abs(value * precision - Math.round(value * precision)) > .000001) add(field, `必须精确到整数${precision === 10 ? '毫米' : '克'}`);
    }
    if (!Number.isInteger(row.quantity) || row.quantity < 1) issues.push({ row: rowNumber, field: "数量", message: "必须是大于 0 的整数" });
    if (row.stackable && (!Number.isInteger(row.max_layers) || row.max_layers < 1 || row.max_layers > 100)) add('最大层数', '必须是 1 至 100 的整数');
    if ((row.stackable || row.kind === 'pallet') && (!Number.isFinite(row.max_top_load_kg) || row.max_top_load_kg < 0 || !Number.isSafeInteger(Math.round(row.max_top_load_kg * 1000)) || Math.abs(row.max_top_load_kg * 1000 - Math.round(row.max_top_load_kg * 1000)) > .000001)) add('顶部承重', '必须是非负重量，精确到整数克');
    if (!Number.isSafeInteger(row.unload_order ?? 0) || (row.unload_order ?? 0) < 0) add('卸货顺序', '必须是非负整数');
  });
  if (rows.length > 30) issues.push({ row: 0, field: "货物种类", message: "单次最多支持 30 种" });
  if (rows.reduce((sum, row) => sum + row.quantity, 0) > 5000) issues.push({ row: 0, field: "总数量", message: "单次最多支持 5000 件" });
  return issues;
}

export function validateCalculationSettings(container: ContainerSpec | null, itemGapCm: number, clearanceCm: number): string | null {
  const precise = (value: number) => Number.isSafeInteger(Math.round(value * 10)) && Math.abs(value * 10 - Math.round(value * 10)) <= .000001;
  if (!Number.isFinite(itemGapCm) || itemGapCm < 0 || itemGapCm > 100 || !precise(itemGapCm)) return '货物间隙须为 0 至 100 cm，精确到毫米';
  if (container) {
    for (const [field, label] of [['inner_length_mm', '柜内长'], ['inner_width_mm', '柜内宽'], ['inner_height_mm', '柜内高'], ['door_width_mm', '柜门宽'], ['door_height_mm', '柜门高'], ['max_payload_g', '最大载重']] as const) {
      if (!Number.isSafeInteger(container[field]) || container[field] <= 0) return `${label}须为正数，尺寸精确到毫米、重量精确到克`;
    }
  }
  if (!Number.isFinite(clearanceCm) || clearanceCm < 0 || !precise(clearanceCm) || (container && clearanceCm * 20 >= Math.min(container.inner_length_mm, container.inner_width_mm, container.inner_height_mm))) return '柜体安全边距须为非负数、精确到毫米，且不能占满柜体';
  return null;
}

