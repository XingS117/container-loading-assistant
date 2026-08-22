import { COMMON_CARGO_PRESETS, cloneCargoPreset } from "./cargoPresets";

test("contains three combinations and twelve single-product presets", () => {
  expect(COMMON_CARGO_PRESETS.filter((item) => item.kind === "组合")).toHaveLength(3);
  expect(COMMON_CARGO_PRESETS.filter((item) => item.kind === "单品")).toHaveLength(12);
});

test("contains the confirmed common dimensions and quantities", () => {
  const fourSku = COMMON_CARGO_PRESETS.find((item) => item.id === "common-case-4");
  const fiveSku = COMMON_CARGO_PRESETS.find((item) => item.id === "common-case-5");
  const abc = COMMON_CARGO_PRESETS.find((item) => item.id === "common-abc");

  expect(fourSku?.items.map((item) => [item.length_cm, item.width_cm, item.height_cm, item.quantity])).toEqual([
    [76, 76, 100, 18],
    [115, 115, 110, 8],
    [110, 110, 100, 12],
    [105, 105, 80, 12],
  ]);
  expect(fiveSku?.items.map((item) => item.quantity)).toEqual([22, 25, 5, 1, 2]);
  expect(abc?.items.map((item) => item.weight_kg)).toEqual([150, 280, 400]);
});

test("marks missing weights without sharing mutable rows", () => {
  const preset = COMMON_CARGO_PRESETS.find((item) => item.id === "common-case-4");
  expect(preset).toBeDefined();
  expect(preset?.items.every((item) => item.weight_kg === null)).toBe(true);

  const first = cloneCargoPreset(preset!);
  const second = cloneCargoPreset(preset!);
  expect(first.map((item) => item.id)).not.toEqual(second.map((item) => item.id));
  first[0].sku = "CUSTOMIZED";
  expect(second[0].sku).not.toBe("CUSTOMIZED");
  expect(preset?.items[0].sku).not.toBe("CUSTOMIZED");
});
