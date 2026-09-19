import { createCargo, validateCargo, validateCargoIssues, validateCalculationSettings } from "./cargo";

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

test('submission rejects every issue shown by the quality report', () => {
  for (const update of [{quantity:1.5}, {length_cm:NaN}, {weight_kg:Infinity}, {length_cm:.01}, {length_cm:1e-10}, {max_layers:101}, {max_top_load_kg:-1}, {unload_order:-1}]) {
    const rows = [{ ...createCargo('A'), ...update }];
    expect(validateCargoIssues(rows).length).toBeGreaterThan(0);
    expect(validateCargo(rows)).not.toBeNull();
  }
});

test('rejects invalid cabinet settings and checks clearance against the selected container', () => {
  const container = {id:'small',name:'small',inner_length_mm:1000,inner_width_mm:1000,inner_height_mm:1000,door_width_mm:1000,door_height_mm:1000,max_payload_g:1000000,clearance_mm:0};
  expect(validateCalculationSettings(container, 0, 50)).toMatch(/不能占满/);
  expect(validateCalculationSettings({...container, door_width_mm:0}, 0, 0)).toMatch(/柜门宽/);
  expect(validateCalculationSettings(container, .01, 0)).toMatch(/精确到毫米/);
  expect(validateCalculationSettings(container, 0, 0)).toBeNull();
});
