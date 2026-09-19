import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { classifySolutionWarning, explainFloorRisk, loadingStepLabels, recommendProfile, SolutionWorkspace } from "./SolutionWorkspace";
import type { CargoInput, ContainerSpec, PackResponse, SolutionProfile } from "../types";
import * as api from '../lib/api';
import { createCargo } from '../lib/cargo';


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

test('removal updates only the edited profile and its printed counts, and restoring recovers them', async () => {
  const cargo = {...createCargo('REMOVE-PRINT'),id:'a',quantity:1};
  const response = makeResponse(0,0);
  response.solutions.forEach(s => {
    s.placements = [{id:'a-0',cargo_id:'a',instance_index:0,x_mm:0,y_mm:0,z_mm:0,length_mm:600,width_mm:400,height_mm:400,rotation:'LWH',weight_g:18000,step:1}];
    s.loaded_counts = {a:1}; s.unloaded_counts = {a:0}; s.metrics.loaded_pieces = 1;
  });
  const review = vi.spyOn(api,'reviewLayout').mockImplementation(async (_c,_items,placements) => ({valid:true,errors:[],placements,metrics:{...response.solutions[0].metrics,loaded_pieces:placements.length},zones:[]}));
  try {
    const {container:root} = render(<SolutionWorkspace response={response} container={container} presets={presets} cargoItems={[cargo]} onBack={() => {}} onRecalculate={async () => {}} recalculating={false} />);
    await userEvent.click(screen.getByRole('button',{name:'编辑布局'}));
    await userEvent.click(screen.getByRole('button',{name:'REMOVE-PRINT · 第 1 件'}));
    await userEvent.click(screen.getByRole('button',{name:'移出选中单件'}));
    await waitFor(() => expect(screen.getByRole('button',{name:'应用调整'})).toBeEnabled());
    await userEvent.click(screen.getByRole('button',{name:'应用调整'}));
    expect(screen.getByText('0 / 1')).toBeInTheDocument();
    expect(screen.getByText('余 1 件')).toBeInTheDocument();
    const printRows = Array.from(root.querySelectorAll('.print-solution-page .print-table tbody tr')).filter(row => row.textContent?.includes('REMOVE-PRINT'));
    expect(printRows).toHaveLength(3);
    expect(printRows[0].textContent).toMatch(/0 件.*1 件/);
    expect(printRows[1].textContent).toMatch(/1 件.*0 件/);
    expect(printRows[2].textContent).toMatch(/1 件.*0 件/);
    expect(response.solutions[0].placements).toHaveLength(1);
    await userEvent.click(screen.getByRole('button',{name:'恢复原始布局'}));
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.queryByText('余 1 件')).not.toBeInTheDocument();
  } finally { review.mockRestore(); }
});


test("recommends stable when high_fill is imbalanced and stable improves it", () => {
  expect(recommendProfile(makeResponse(20, 3))).toBe("stable");
});


test("keeps high_fill when already balanced or improvement is small", () => {
  expect(recommendProfile(makeResponse(8, 3))).toBe("high_fill");
  expect(recommendProfile(makeResponse(20, 17))).toBe("high_fill");
});

test("uses the server recommendation when a preferred goal was selected", () => {
  const response = makeResponse(8, 3);
  response.recommended_profile = "easy";
  expect(recommendProfile(response)).toBe("easy");
});


test("classifies solution notices by operational severity", () => {
  expect(classifySolutionWarning("订单总重 28.65t，超过柜体最大载重 28.60t")).toBe("critical");
  expect(classifySolutionWarning("仍有 1 件货物未装入本柜")).toBe("caution");
  expect(classifySolutionWarning("上层未充分集中在中部，请现场复核")).toBe("caution");
  expect(classifySolutionWarning("柜门预留操作空间 300mm")).toBe("info");
  expect(classifySolutionWarning("当前方案仍剩载重 0.75t")).toBe("info");
});

test("explains whether a floor gap needs stability review", () => {
  const solution = makeSolution("stable", 3);
  solution.metrics.floor_largest_gap_mm = 180;
  solution.metrics.floor_largest_transverse_gap_mm = 0;
  expect(explainFloorRisk(solution)).toContain("需要现场复核");
  solution.metrics.floor_largest_gap_mm = 20;
  expect(explainFloorRisk(solution)).toContain("一般可接受");
});

test("lists loading steps in assigned order", () => {
  const solution = makeSolution("easy", 3);
  solution.zones = [
    { step: 2, cargo_id: "b", x_mm: 1000, y_mm: 0, length_mm: 100, width_mm: 100, piece_count: 2 },
    { step: 1, cargo_id: "a", x_mm: 0, y_mm: 0, length_mm: 100, width_mm: 100, piece_count: 3 },
    { step: 1, cargo_id: "a", x_mm: 200, y_mm: 0, length_mm: 100, width_mm: 100, piece_count: 2 },
  ];
  expect(loadingStepLabels(solution, [{ id: "a", sku: "A", quantity: 3 } as CargoInput, { id: "b", sku: "B", quantity: 2 } as CargoInput])).toEqual([
    "第 1 步：A × 5 件",
    "第 2 步：B × 2 件",
  ]);
});

test("prints the loading direction from container interior towards the door", () => {
  render(<SolutionWorkspace response={makeResponse(8, 3)} container={container} presets={presets} cargoItems={cargoItems} onBack={() => {}} onRecalculate={async () => {}} recalculating={false} />);
  expect(screen.getAllByRole('heading', { name: '装载步骤（由柜内向柜门，先下后上）' })).toHaveLength(3);
});

test('worksheet follows the active profile while print keeps every solution separate', async () => {
  const response = makeResponse(8, 3);
  response.solutions.forEach((solution, index) => {
    solution.placements = [{ id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: index * 1000, y_mm: 0, z_mm: 0, length_mm: 500, width_mm: 500, height_mm: 500, weight_g: 1000, rotation: 'LWH', step: 1 }];
  });
  render(<SolutionWorkspace response={response} container={container} presets={presets} cargoItems={[{ id: 'a', sku: 'A' } as CargoInput]} onBack={() => {}} onRecalculate={async () => {}} recalculating={false} />);
  const panel = screen.getByLabelText('当前方案作业单');
  expect(panel).not.toHaveAttribute('open');
  await userEvent.click(within(panel).getByText('仓库作业单'));
  expect(within(panel).getByText('0 / 0 / 0')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /重心稳妥/ }));
  expect(within(panel).queryByText('0 / 0 / 0')).not.toBeInTheDocument();
  expect(within(panel).getByText('100 / 0 / 0')).toBeInTheDocument();
  expect(screen.getAllByLabelText('纸面复核签名')).toHaveLength(3);
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
  expect(screen.getAllByText("前后偏差").length).toBeGreaterThan(0);
  expect(screen.getAllByText("左右偏差").length).toBeGreaterThan(0);
});


test("shows the AI strategy status separately from safety notices", () => {
  const response = {
    ...makeResponse(8, 3),
    ai_strategy: {
      status: "considered",
      applied: true,
      provider: "DeepSeek",
      model: "deepseek-v4-flash",
      message: "AI 策略建议已采纳，并已参与候选布局生成；最终布局仍以本地物理校验和评分为准",
      sku_order: ["a"],
      orientations: {},
      row_groups: [["a", "b"], ["c", "d"]],
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

  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("AI 策略建议已采纳");
  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("DeepSeek / deepseek-v4-flash");
  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("已采纳 2 个行组建议");
  expect(screen.getByRole("status", { name: "AI 策略状态" })).toBeInTheDocument();
  expect(screen.queryByLabelText("方案提示")).not.toBeInTheDocument();
});


test("shows profile-specific AI adoption and easy-layout disclosure", async () => {
  const response = {
    ...makeResponse(8, 3),
    ai_strategy: {
      status: "considered",
      applied: true,
      provider: "DeepSeek",
      model: "deepseek-v4-flash",
      message: "AI 策略建议已获取，正在由本地物理校验决定是否采纳",
      sku_order: ["a"],
      orientations: {},
      row_groups: [],
      coordinate_candidates_applied: ["high_fill"],
      profiles: {
        high_fill: { sku_order: ["a"] },
        stable: { sku_order: ["a"] },
        easy: { zone_order: ["a"], max_zones: 2 },
      },
    },
  } as PackResponse;
  response.solutions[2].warnings = ["易操作方案少装 2 件换取连续分区"];

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

  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("三种方案按目标分别优化");
  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("已采纳经校验的 AI 坐标候选：装载率优先");
  await userEvent.click(screen.getByRole("button", { name: /易操作/ }));
  expect(screen.getByText(/少装 2 件换取连续分区/)).toBeInTheDocument();
});

test("records solution feedback selection", async () => {
  const response = makeResponse(8, 3);
  render(<SolutionWorkspace response={response} container={container} presets={presets} cargoItems={cargoItems} onBack={() => undefined} onRecalculate={async () => undefined} recalculating={false} />);
  await userEvent.click(screen.getByRole("button", { name: "满意" }));
  expect(screen.getByRole("button", { name: "满意" })).toHaveClass("is-selected");
});

test("opens an adjustment panel and records the requested topics", async () => {
  const response = makeResponse(8, 3);
  render(<SolutionWorkspace response={response} container={container} presets={presets} cargoItems={cargoItems} onBack={() => undefined} onRecalculate={async () => undefined} recalculating={false} />);
  await userEvent.click(screen.getByRole("button", { name: "需要调整" }));
  expect(screen.getByRole("region", { name: "方案调整说明" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "中间空隙太大" }));
  await userEvent.type(screen.getByRole("textbox", { name: "补充调整要求" }), "把深绿色货物向中间集中");
  await userEvent.click(screen.getByRole("button", { name: "提交调整说明" }));
  expect(screen.getByText(/调整说明已记录/)).toHaveTextContent("当前布局未改变");
});
