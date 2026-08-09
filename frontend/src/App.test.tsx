import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import App from "./App";


const preset = {
  id: "20gp",
  name: "20GP",
  inner_length_mm: 5898,
  inner_width_mm: 2352,
  inner_height_mm: 2393,
  door_width_mm: 2340,
  door_height_mm: 2280,
  max_payload_g: 28200000,
  clearance_mm: 0,
};

const secondPreset = {
  id: "40hq",
  name: "40HQ",
  inner_length_mm: 12032,
  inner_width_mm: 2352,
  inner_height_mm: 2698,
  door_width_mm: 2340,
  door_height_mm: 2585,
  max_payload_g: 28600000,
  clearance_mm: 0,
};

const response = {
  request_id: "abc123",
  solutions: ["high_fill", "stable", "easy"].map((profile, index) => ({
    profile,
    name: ["装得多", "更稳妥", "易操作"][index],
    placements: [],
    loaded_counts: { cargo_1: 0 },
    unloaded_counts: { cargo_1: 10 },
    metrics: {
      loaded_pieces: 0,
      loaded_weight_g: 0,
      volume_utilization_pct: 0,
      weight_utilization_pct: 0,
      center_of_gravity: { x_mm: 0, y_mm: 0, z_mm: 0 },
      length_imbalance_pct: 0,
      width_imbalance_pct: 0,
      weight_imbalance_pct: 0,
      loading_steps: 0,
      cargo_zones: 0,
    },
    zones: [],
    pros: ["测试优点"],
    cons: ["测试缺点"],
    warnings: [],
    identical_to: index ? "high_fill" : null,
  })),
};


test("loads presets and switches from input to comparable solutions", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify([preset, secondPreset]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }));

  render(<App />);

  expect(screen.getByRole("heading", { name: "装柜方案助手" })).toBeInTheDocument();
  expect(screen.getByText("选择柜型，录入货物，生成可执行装柜方案。")).toBeInTheDocument();
  expect(screen.getByAltText("一帆风顺，满载启航海运横幅")).toBeInTheDocument();
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "生成 3 个方案" }));

  await waitFor(() => expect(screen.getByText("方案比较")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /装得多/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /更稳妥/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /易操作/ })).toBeInTheDocument();

  const containerSelect = screen.getByRole("combobox", { name: "重算柜型" });
  await userEvent.selectOptions(containerSelect, "40hq");
  await userEvent.click(screen.getByRole("button", { name: "确认重算" }));

  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByRole("combobox", { name: "重算柜型" })).toHaveValue("40hq");
});


test("selecting pallet kind keeps the selection and applies pallet defaults", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });

  const kindSelect = screen.getByRole("combobox", { name: "货物类型 SKU-001" });
  await userEvent.selectOptions(kindSelect, "pallet");

  // 关键回归：选中整托后下拉必须保持整托（此前多次 update 互相覆盖导致跳回散箱）
  expect(screen.getByRole("combobox", { name: "货物类型 SKU-001" })).toHaveValue("pallet");
  // 整托不可叠（可叠复选框不勾选）
  expect(screen.getByRole("checkbox", { name: /可叠/ })).not.toBeChecked();
  // 整托默认顶部承重 500kg（允许散箱上托）
  expect(screen.getByLabelText("顶部承重 SKU-001")).toHaveValue(500);
});
