import { cargoRowsFromMatrix, inspectCargoMatrix, EXCEL_HEADERS } from "./excel";


test("maps the fixed Chinese Excel template into cargo rows", () => {
  const rows = cargoRowsFromMatrix([
    ["SKU", "货物名称", "类型", "长(cm)", "宽(cm)", "高(cm)", "单重(kg)", "数量", "摆放方式", "可叠放", "最大层数", "顶部承重(kg)", "易碎", "本柜必装"],
    ["A-01", "出口纸箱", "散箱", 60, 40, 35, 18.5, 120, "保持正放", "是", 5, 90, "否", "是"],
  ]);

  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({
    sku: "A-01",
    name: "出口纸箱",
    kind: "carton",
    length_cm: 60,
    weight_kg: 18.5,
    quantity: 120,
    orientation_mode: "upright",
    stackable: true,
    max_layers: 5,
    fragile: false,
    must_load: true,
  });
});


test("rejects spreadsheets with missing required columns", () => {
  expect(() => cargoRowsFromMatrix([["SKU", "数量"], ["A", 1]])).toThrow("缺少必需列");
});


test("rejects unknown enums and fractional quantities instead of rewriting them", () => {
  const header = ["SKU", "货物名称", "类型", "长(cm)", "宽(cm)", "高(cm)", "单重(kg)", "数量", "摆放方式", "可叠放", "最大层数", "顶部承重(kg)", "易碎", "本柜必装"];
  expect(() => cargoRowsFromMatrix([header, ["A", "箱", "袋装", 10, 10, 10, 1, 1, "保持正放", "是", 2, 2, "否", "否"]])).toThrow("类型");
  expect(() => cargoRowsFromMatrix([header, ["A", "箱", "散箱", 10, 10, 10, 1, 1.5, "保持正放", "是", 2, 2, "否", "否"]])).toThrow("数量");
  expect(() => cargoRowsFromMatrix([header, ["A", "箱", "散箱", 10, 10, 10, 1, 1, "斜放", "是", 2, 2, "否", "否"]])).toThrow("摆放方式");
});

const sample = ['A', '箱', '散箱', 600, 400, 400, 18000, 2, '保持正放', '是', 2, 0, '否', '否'];
test('converts explicit units and allows zero top load without changing the goods', () => {
  const headers = EXCEL_HEADERS.map(h => h.replace('(cm)', '（mm）').replace('(kg)', '(g)'));
  const report = inspectCargoMatrix([headers, sample]);
  expect(report.issues).toEqual([]);
  expect(report.rows[0]).toMatchObject({ length_cm: 60, weight_kg: 18, max_top_load_kg: 0 });
  expect(report.conversions.length).toBeGreaterThan(0);
});
test('reports all cell errors with original row numbers and never returns partial orders', () => {
  const report = inspectCargoMatrix([[...EXCEL_HEADERS], [], ['A', '箱', '未知', -1, 0, 40, 'abc', 1.5, '斜放', '是', 101, -2, '否', '否']]);
  expect(report.issues.length).toBeGreaterThanOrEqual(7);
  expect(report.issues.every(i => i.row === 3)).toBe(true);
  expect(report.rows).toEqual([]);
});
test('rejects ambiguous duplicate columns and unsupported units', () => {
  expect(inspectCargoMatrix([[...EXCEL_HEADERS, '长(mm)'], [...sample, 600]]).issues.some(i => i.message.includes('重复'))).toBe(true);
  expect(inspectCargoMatrix([EXCEL_HEADERS.map(h => h === '长(cm)' ? '长(in)' : h), sample]).issues.length).toBeGreaterThan(0);
});
test('rejects contradictory cell units and sub-millimeter dimensions', () => {
  const row = [...sample]; row[3] = '60 mm'; row[4] = 0.01;
  const report = inspectCargoMatrix([[...EXCEL_HEADERS], row]);
  expect(report.issues.some(i => i.message.includes('单位'))).toBe(true);
  expect(report.issues.some(i => i.message.includes('毫米'))).toBe(true);
});
test('supports meter and tonne columns and optional unload order', () => {
  const headers = EXCEL_HEADERS.map(h => h.replace('(cm)', '(m)').replace('(kg)', '(t)'));
  const report = inspectCargoMatrix([[...headers, '卸货顺序'], ['A', '箱', '散箱', .6, .4, .4, .018, 2, '保持正放', '是', 2, 0, '否', '否', 3]]);
  expect(report.rows[0]).toMatchObject({ length_cm: 60, weight_kg: 18, unload_order: 3 });
});
