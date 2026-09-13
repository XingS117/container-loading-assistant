import { createCargo, validateCargoIssues } from "./cargo";

test("reports every invalid cargo field with its row", () => {
  const cargo = createCargo("BAD");
  cargo.weight_kg = null;
  cargo.quantity = 1.5;
  cargo.length_cm = 0;

  expect(validateCargoIssues([cargo])).toEqual(expect.arrayContaining([
    { row: 1, field: "单重", message: "必须大于 0" },
    { row: 1, field: "长", message: "必须大于 0" },
    { row: 1, field: "数量", message: "必须是大于 0 的整数" },
  ]));
});
