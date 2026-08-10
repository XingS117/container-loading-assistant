import type { CargoInput, OrientationMode } from "../types";

export const EXCEL_HEADERS = [
  "SKU",
  "货物名称",
  "类型",
  "长(cm)",
  "宽(cm)",
  "高(cm)",
  "单重(kg)",
  "数量",
  "摆放方式",
  "可叠放",
  "最大层数",
  "顶部承重(kg)",
  "易碎",
  "本柜必装",
] as const;

function text(value: unknown): string {
  return String(value ?? "").trim();
}

function numberValue(value: unknown, column: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${column} 必须是大于 0 的数字`);
  }
  return parsed;
}

function booleanValue(value: unknown, column: string): boolean {
  const normalized = text(value).toLowerCase();
  if (["是", "yes", "true", "1", "y"].includes(normalized)) return true;
  if (["否", "no", "false", "0", "n"].includes(normalized)) return false;
  throw new Error(`${column} 只能填写“是”或“否”`);
}

function orientationMode(value: unknown): OrientationMode {
  const normalized = text(value);
  if (normalized === "保持正放") return "upright";
  if (normalized === "允许侧放") return "side";
  if (normalized === "任意朝向") return "any";
  throw new Error("摆放方式只能填写“保持正放”“允许侧放”或“任意朝向”");
}

function integerValue(value: unknown, column: string): number {
  const parsed = numberValue(value, column);
  if (!Number.isInteger(parsed)) throw new Error(`${column} 必须是整数`);
  return parsed;
}

export function cargoRowsFromMatrix(matrix: unknown[][]): CargoInput[] {
  if (!matrix.length) throw new Error("Excel 文件没有内容");
  const headers = matrix[0].map(text);
  const missing = EXCEL_HEADERS.filter((header) => !headers.includes(header));
  if (missing.length) throw new Error(`缺少必需列：${missing.join("、")}`);
  const column = Object.fromEntries(headers.map((header, index) => [header, index]));

  const rows = matrix.slice(1).filter((row) => row.some((value) => text(value)));
  return rows.map((row, index) => {
    const value = (header: (typeof EXCEL_HEADERS)[number]) => row[column[header]];
    const stackable = booleanValue(value("可叠放"), "可叠放");
    const sku = text(value("SKU"));
    const kindText = text(value("类型"));
    if (!["散箱", "整托"].includes(kindText)) throw new Error("类型只能填写“散箱”或“整托”");
    if (!sku) throw new Error(`第 ${index + 2} 行 SKU 不能为空`);
    return {
      id: `cargo_excel_${index + 1}_${sku.replace(/[^a-zA-Z0-9_-]/g, "_")}`,
      sku,
      name: text(value("货物名称")) || sku,
      kind: kindText === "整托" ? "pallet" : "carton",
      length_cm: numberValue(value("长(cm)"), "长(cm)"),
      width_cm: numberValue(value("宽(cm)"), "宽(cm)"),
      height_cm: numberValue(value("高(cm)"), "高(cm)"),
      weight_kg: numberValue(value("单重(kg)"), "单重(kg)"),
      quantity: integerValue(value("数量"), "数量"),
      orientation_mode: orientationMode(value("摆放方式")),
      stackable,
      max_layers: stackable ? integerValue(value("最大层数"), "最大层数") : 1,
      max_top_load_kg: stackable ? numberValue(value("顶部承重(kg)"), "顶部承重(kg)") : 0,
      fragile: booleanValue(value("易碎"), "易碎"),
      must_load: booleanValue(value("本柜必装"), "本柜必装"),
      unload_order: 0,
    };
  });
}

export async function readCargoExcel(file: File): Promise<CargoInput[]> {
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await file.arrayBuffer());
  const worksheet = workbook.worksheets[0];
  if (!worksheet) throw new Error("Excel 文件没有工作表");
  const matrix: unknown[][] = [];
  worksheet.eachRow((row) => {
    const values = row.values as unknown[];
    matrix.push(values.slice(1).map((value) => {
      if (value && typeof value === "object" && "text" in value) return (value as { text: string }).text;
      return value;
    }));
  });
  return cargoRowsFromMatrix(matrix);
}

export async function downloadCargoTemplate(): Promise<void> {
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  const worksheet = workbook.addWorksheet("货物清单");
  worksheet.addRow([...EXCEL_HEADERS]);
  worksheet.addRow(["SKU-001", "标准纸箱", "散箱", 60, 40, 40, 18, 100, "保持正放", "是", 5, 90, "否", "否"]);
  worksheet.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
  worksheet.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF17211D" } };
  worksheet.columns.forEach((column) => { column.width = 16; });
  const buffer = await workbook.xlsx.writeBuffer();
  const blob = new Blob([buffer as ArrayBuffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "装柜方案助手-货物导入模板.xlsx";
  link.click();
  URL.revokeObjectURL(url);
}
