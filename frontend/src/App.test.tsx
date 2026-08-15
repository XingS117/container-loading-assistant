import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import App from "./App";
import { makeContainer, makeResponse } from "./test/fixtures";


const preset = makeContainer({ id: "20gp", name: "20GP" });
const secondPreset = makeContainer({
  id: "40hq",
  name: "40HQ",
  inner_length_mm: 12032,
  inner_height_mm: 2698,
  door_height_mm: 2585,
  max_payload_g: 28600000,
});

const response = makeResponse("high_fill");


test("loads presets and switches from input to the single goal solution", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify([preset, secondPreset]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }));

  render(<App />);

  expect(screen.getByRole("heading", { name: "装柜方案助手" })).toBeInTheDocument();
  expect(screen.getByText("选择柜型，录入货物，生成可执行装柜方案。")).toBeInTheDocument();
  expect(screen.getByAltText("一帆风顺，满载启航海运横幅")).toBeInTheDocument();
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "生成装柜方案" }));

  await waitFor(() => expect(screen.getByRole("heading", { name: "装柜方案" })).toBeInTheDocument());
  // 三个优化目标按钮，不再有「底层优先」
  expect(screen.getAllByRole("button", { name: /装载率优先|重心稳妥|易操作/ })).toHaveLength(3);
  expect(screen.getByRole("button", { name: /装载率优先/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: /重心稳妥/ })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: /易操作/ })).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryByRole("button", { name: /底层优先/ })).not.toBeInTheDocument();
  expect(screen.queryByText("互叠高装载")).not.toBeInTheDocument();

  const containerSelect = screen.getByRole("combobox", { name: "重算柜型" });
  await userEvent.selectOptions(containerSelect, "40hq");
  await userEvent.click(screen.getByRole("button", { name: "确认重算" }));

  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByRole("combobox", { name: "重算柜型" })).toHaveValue("40hq");
});


test("switching the goal recalculates with optimization_goal in the request body", async () => {
  const packBodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/v1/container-presets") {
      return new Response(JSON.stringify([preset, secondPreset]), { status: 200 });
    }
    if (url === "/api/v1/pack") {
      packBodies.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify(response), { status: 200 });
    }
    throw new Error(`未预期的请求: ${url}`);
  });

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "生成装柜方案" }));
  await screen.findByRole("heading", { name: "装柜方案" });
  expect(packBodies[0]).toMatchObject({ optimization_goal: "high_fill" });

  await userEvent.click(screen.getByRole("button", { name: /重心稳妥/ }));

  await waitFor(() => expect(packBodies).toHaveLength(2));
  expect(packBodies[1]).toMatchObject({ optimization_goal: "stable" });
  expect(screen.getByRole("button", { name: /重心稳妥/ })).toHaveAttribute("aria-pressed", "true");
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
