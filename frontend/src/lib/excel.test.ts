import { cargoRowsFromMatrix } from "./excel";


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
