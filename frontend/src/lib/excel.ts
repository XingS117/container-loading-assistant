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

function numberValue(value: unknown, column: string, allowZero = false): number {
  const parsed = Number(value);
  if (!text(value) || !Number.isFinite(parsed) || (allowZero ? parsed < 0 : parsed <= 0)) {
    throw new Error(`${column} 必须是${allowZero ? '大于等于' : '大于'} 0 的数字`);
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

export interface ExcelIssue { row: number; column: string; message: string }
export interface ExcelReport { rows: CargoInput[]; issues: ExcelIssue[]; conversions: string[]; rowCount: number }
const units: Record<string, { unit: string; factor: number }> = {
  mm: { unit: 'mm', factor: .1 }, '毫米': { unit: 'mm', factor: .1 },
  cm: { unit: 'cm', factor: 1 }, '厘米': { unit: 'cm', factor: 1 },
  m: { unit: 'm', factor: 100 }, '米': { unit: 'm', factor: 100 },
  g: { unit: 'g', factor: .001 }, '克': { unit: 'g', factor: .001 },
  kg: { unit: 'kg', factor: 1 }, '千克': { unit: 'kg', factor: 1 }, '公斤': { unit: 'kg', factor: 1 },
  t: { unit: 't', factor: 1000 }, '吨': { unit: 't', factor: 1000 },
};

export function inspectCargoMatrix(matrix: unknown[][]): ExcelReport {
  const report: ExcelReport = { rows: [], issues: [], conversions: [], rowCount: 0 };
  const add = (row: number, column: string, message: string) => report.issues.push({ row, column, message });
  if (!matrix.length) { add(1, '工作表', 'Excel 文件没有内容'); return report; }
  const columns = new Map<string, { index: number; unit?: string; factor: number }>();
  matrix[0].forEach((value, index) => {
    const header = text(value).replaceAll('（', '(').replaceAll('）', ')').replace(/\s/g, '');
    let canonical = header;
    let factor = 1;
    let unit: string | undefined;
    const match = header.match(/^(长|宽|高|单重|顶部承重)\((.+)\)$/);
    if (match) {
      const dimension = ['长', '宽', '高'].includes(match[1]);
      const mapping = units[match[2].toLowerCase()];
      if (!mapping || !(dimension ? ['mm', 'cm', 'm'] : ['g', 'kg', 't']).includes(mapping.unit)) {
        add(1, header, '不支持的单位，请使用 mm/cm/m 或 g/kg/t'); return;
      }
      canonical = `${match[1]}(${dimension ? 'cm' : 'kg'})`;
      ({ factor, unit } = mapping);
      if (factor !== 1) report.conversions.push(`${header} → ${canonical}（数值 × ${factor}）`);
    }
    if (![...EXCEL_HEADERS, '卸货顺序'].includes(canonical as typeof EXCEL_HEADERS[number])) return;
    if (columns.has(canonical)) add(1, canonical, '同一字段重复，请只保留一列');
    else columns.set(canonical, { index, factor, unit });
  });
  for (const header of EXCEL_HEADERS) if (!columns.has(header)) add(1, header, `缺少必需列：${header}`);
  if (report.issues.length) return report;
  const rows: CargoInput[] = [];
  matrix.slice(1).forEach((row, index) => {
    if (!row?.some(value => text(value))) return;
    report.rowCount++;
    const line = index + 2;
    const value = (header: string) => row[columns.get(header)!.index];
    const read = <T,>(header: string, parse: (v: unknown) => T, fallback: T): T => {
      try { return parse(value(header)); }
      catch (e) { add(line, header, e instanceof Error ? e.message : '无效数据'); return fallback; }
    };
    const measure = (header: string, zero = false) => read(header, raw => {
      const col = columns.get(header)!;
      const match = text(raw).match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*([a-z\u4e00-\u9fff]+)?$/i);
      if (!match) throw new Error('请填写数值，不接受公式或无法识别的文本');
      if (match[2] && units[match[2].toLowerCase()]?.unit !== col.unit) throw new Error('单元格单位与列头单位不一致');
      const result = numberValue(match[1], header, zero) * col.factor;
      const precision = header.endsWith('(cm)') ? 10 : 1000;
      if ((!zero && Math.round(result * precision) < 1) || !Number.isFinite(result) || !Number.isSafeInteger(Math.round(result * precision)) || Math.abs(result * precision - Math.round(result * precision)) > .000001) throw new Error(`必须精确到整数${precision === 10 ? '毫米' : '克'}，请核对数值`);
      return Math.round(result * precision) / precision;
    }, 0);
    const stackable = read('可叠放', v => booleanValue(v, '可叠放'), false);
    const sku = read('SKU', v => { if (!text(v)) throw new Error('SKU 不能为空'); return text(v); }, '');
    const kind = read('类型', v => { if (!['散箱', '整托'].includes(text(v))) throw new Error('类型只能填写“散箱”或“整托”'); return text(v) === '整托' ? 'pallet' as const : 'carton' as const; }, 'carton');
    const quantity = read('数量', v => { const n = integerValue(v, '数量'); if (n > 5000) throw new Error('单次最多支持 5000 件'); return n; }, 0);
    const layers = stackable ? read('最大层数', v => { const n = integerValue(v, '最大层数'); if (n > 100) throw new Error('最大层数不能超过 100'); return n; }, 1) : 1;
    rows.push({
      id: `cargo_excel_${index + 1}_${sku.replace(/[^a-zA-Z0-9_-]/g, "_")}`,
      sku,
      name: text(value("货物名称")) || sku,
      kind,
      length_cm: measure('长(cm)'), width_cm: measure('宽(cm)'), height_cm: measure('高(cm)'), weight_kg: measure('单重(kg)'),
      quantity,
      orientation_mode: read('摆放方式', orientationMode, 'upright'),
      stackable,
      max_layers: layers,
      max_top_load_kg: stackable || kind === 'pallet' ? measure('顶部承重(kg)', true) : 0,
      fragile: read('易碎', v => booleanValue(v, '易碎'), false),
      must_load: read('本柜必装', v => booleanValue(v, '本柜必装'), false),
      unload_order: columns.has('卸货顺序') ? read('卸货顺序', v => { const n = numberValue(v, '卸货顺序', true); if (!Number.isSafeInteger(n)) throw new Error('卸货顺序必须是非负整数'); return n; }, 0) : 0,
    });
  });
  if (!rows.length) add(1, '货物', '没有货物数据，请在表头下填写货物');
  if (rows.length > 30) add(1, '货物种类', '单次最多支持 30 种货物，请拆分订单');
  if (rows.reduce((sum, r) => sum + r.quantity, 0) > 5000) add(1, '总数量', '单次最多支持 5000 件货物，请拆分订单');
  if (!report.issues.length) report.rows = rows;
  return report;
}

export function cargoRowsFromMatrix(matrix: unknown[][]): CargoInput[] {
  const report = inspectCargoMatrix(matrix);
  if (report.issues.length) throw new Error(report.issues.map(i => `第 ${i.row} 行 ${i.column}：${i.message}`).join('\n'));
  return report.rows;
}

export async function readCargoExcelReport(file: File): Promise<ExcelReport> {
  if (file.size > 10 * 1024 * 1024) throw new Error('Excel 文件不能超过 10 MB，请移除无关工作表后重试');
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await file.arrayBuffer());
  const worksheet = workbook.worksheets[0];
  if (!worksheet) throw new Error("Excel 文件没有工作表");
  if (worksheet.rowCount > 10000 || worksheet.columnCount > 100) throw new Error('工作表范围过大，请仅保留货物数据后重试');
  const matrix: unknown[][] = [];
  worksheet.eachRow({ includeEmpty: true }, (row) => {
    const values = row.values as unknown[];
    matrix.push(values.slice(1).map((value) => {
      if (value && typeof value === "object" && "text" in value) return (value as { text: string }).text;
      return value;
    }));
  });
  return inspectCargoMatrix(matrix);
}

export async function readCargoExcel(file: File): Promise<CargoInput[]> {
  const report = await readCargoExcelReport(file);
  if (report.issues.length) throw new Error(report.issues.map(i => `第 ${i.row} 行 ${i.column}：${i.message}`).join('\n'));
  return report.rows;
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
