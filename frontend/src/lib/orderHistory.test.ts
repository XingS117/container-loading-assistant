import { loadSavedOrders, saveOrder } from "./orderHistory";

test("saves and loads a local order copy", () => {
  localStorage.clear();
  saveOrder({ name: "测试订单", container: {} as never, cargoItems: [], itemGapCm: 0, clearanceCm: 0 });
  expect(loadSavedOrders()).toHaveLength(1);
  expect(loadSavedOrders()[0].name).toBe("测试订单");
});
