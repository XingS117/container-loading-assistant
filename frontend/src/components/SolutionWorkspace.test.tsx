import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { classifySolutionWarning, recommendProfile, SolutionWorkspace } from "./SolutionWorkspace";
import type { CargoInput, ContainerSpec, PackResponse, SolutionProfile } from "../types";


const container: ContainerSpec = {
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

const presets: ContainerSpec[] = [container];
const cargoItems: CargoInput[] = [];

function makeSolution(profile: SolutionProfile, lengthImbalance: number): PackResponse["solutions"][number] {
  return {
    profile,
    name: ({ high_fill: "装载率优先", stable: "重心稳妥", easy: "易操作" } as Record<SolutionProfile, string>)[profile],
    placements: [],
    loaded_counts: {},
    unloaded_counts: {},
    metrics: {
      loaded_pieces: 0,
      loaded_weight_g: 0,
      volume_utilization_pct: 0,
      weight_utilization_pct: 0,
      center_of_gravity: { x_mm: 0, y_mm: 0, z_mm: 0 },
      length_imbalance_pct: lengthImbalance,
      width_imbalance_pct: 0,
      weight_imbalance_pct: lengthImbalance,
      loading_steps: 0,
      cargo_zones: 0,
    },
    zones: [],
    pros: [],
    cons: [],
    warnings: [],
    identical_to: null,
  };
}

function makeResponse(highFillImbalance: number, stableImbalance: number): PackResponse {
  return {
    request_id: "test-1",
    solutions: [
      makeSolution("high_fill", highFillImbalance),
      makeSolution("stable", stableImbalance),
      makeSolution("easy", highFillImbalance),
    ],
  };
}


test("recommends stable when high_fill is imbalanced and stable improves it", () => {
  expect(recommendProfile(makeResponse(20, 3))).toBe("stable");
});


test("keeps high_fill when already balanced or improvement is small", () => {
  expect(recommendProfile(makeResponse(8, 3))).toBe("high_fill");
  expect(recommendProfile(makeResponse(20, 17))).toBe("high_fill");
});


test("classifies solution notices by operational severity", () => {
  expect(classifySolutionWarning("订单总重 28.65t，超过柜体最大载重 28.60t")).toBe("critical");
  expect(classifySolutionWarning("仍有 1 件货物未装入本柜")).toBe("caution");
  expect(classifySolutionWarning("上层未充分集中在中部，请现场复核")).toBe("caution");
  expect(classifySolutionWarning("柜门预留操作空间 300mm")).toBe("info");
  expect(classifySolutionWarning("当前方案仍剩载重 0.75t")).toBe("info");
});


test("shows balance warning and the recommended stable tab", async () => {
  const response = makeResponse(20, 3);
  render(
    <SolutionWorkspace
      response={response}
      container={container}
      presets={presets}
      cargoItems={cargoItems}
      onBack={() => undefined}
      onRecalculate={async () => undefined}
      recalculating={false}
    />,
  );

  expect(screen.getByText("推荐")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /优先|稳妥|易操作/ })).toHaveLength(3);
  expect(screen.queryByText("互叠高装载")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /装载率优先/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("前后重量偏差较大（20%），建议查看「重心稳妥」方案");
  expect(screen.getByText("前后偏差")).toBeInTheDocument();
  expect(screen.getByText("左右偏差")).toBeInTheDocument();
});


test("shows the AI strategy status separately from safety notices", () => {
  const response = {
    ...makeResponse(8, 3),
    ai_strategy: {
      status: "considered",
      provider: "DeepSeek",
      model: "deepseek-v4-flash",
      message: "AI 策略建议已获取，并已参与候选布局排序；最终布局仍以本地物理校验和评分为准",
      sku_order: ["a"],
      orientations: {},
    },
  } as PackResponse & { ai_strategy: Record<string, unknown> };

  render(
    <SolutionWorkspace
      response={response}
      container={container}
      presets={presets}
      cargoItems={cargoItems}
      onBack={() => undefined}
      onRecalculate={async () => undefined}
      recalculating={false}
    />,
  );

  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("AI 策略建议已获取");
  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("DeepSeek / deepseek-v4-flash");
  expect(screen.getByRole("status", { name: "AI 策略状态" })).toBeInTheDocument();
  expect(screen.queryByLabelText("方案提示")).not.toBeInTheDocument();
});
