import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SolutionWorkspace } from "./SolutionWorkspace";
import type { CargoInput, ContainerSpec, OptimizationGoal, PackResponse } from "../types";
import { makeContainer, makeResponse, makeSolution } from "../test/fixtures";


const container: ContainerSpec = makeContainer();
const presets: ContainerSpec[] = [container];
const cargoItems: CargoInput[] = [];

function renderWorkspace(
  profile: OptimizationGoal = "high_fill",
  options: {
    lengthImbalance?: number;
    fingerprint?: string;
    onRecalculate?: (container: ContainerSpec, goal: OptimizationGoal) => Promise<void>;
  } = {},
) {
  const solution = makeSolution(profile, {
    layout_fingerprint: options.fingerprint,
    metrics: {
      ...makeSolution(profile).metrics,
      length_imbalance_pct: options.lengthImbalance ?? 0,
      weight_imbalance_pct: options.lengthImbalance ?? 0,
    },
  });
  const response: PackResponse = makeResponse(profile, { solutions: [solution] });
  const onRecalculate = options.onRecalculate ?? (async () => undefined);
  const view = render(
    <SolutionWorkspace
      response={response}
      container={container}
      presets={presets}
      cargoItems={cargoItems}
      goal={profile}
      onBack={() => undefined}
      onRecalculate={onRecalculate}
      recalculating={false}
    />,
  );
  /** 模拟父组件切换目标：换 response（新 request_id）与 goal 重新渲染 */
  const rerenderWith = (nextProfile: OptimizationGoal, nextFingerprint: string) => {
    const nextSolution = makeSolution(nextProfile, {
      layout_fingerprint: nextFingerprint,
    });
    const nextResponse: PackResponse = makeResponse(nextProfile, {
      solutions: [nextSolution],
      request_id: `test-${nextProfile}`,
    });
    view.rerender(
      <SolutionWorkspace
        response={nextResponse}
        container={container}
        presets={presets}
        cargoItems={cargoItems}
        goal={nextProfile}
        onBack={() => undefined}
        onRecalculate={onRecalculate}
        recalculating={false}
      />,
    );
  };
  return { onRecalculate, response, rerenderWith };
}


test("renders three goal buttons with the active goal marked", () => {
  renderWorkspace("high_fill");

  expect(screen.getAllByRole("button", { name: /装载率优先|重心稳妥|易操作/ })).toHaveLength(3);
  expect(screen.getByRole("button", { name: /装载率优先/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: /重心稳妥/ })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: /易操作/ })).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryByRole("button", { name: /底层优先/ })).not.toBeInTheDocument();
  expect(screen.queryByText("互叠高装载")).not.toBeInTheDocument();
  expect(screen.getByText("前后偏差")).toBeInTheDocument();
  expect(screen.getByText("左右偏差")).toBeInTheDocument();
});


test("switching goal calls onRecalculate with the container and the new goal", async () => {
  const onRecalculate = vi.fn(async () => undefined);
  renderWorkspace("high_fill", { onRecalculate });

  await userEvent.click(screen.getByRole("button", { name: /重心稳妥/ }));

  await waitFor(() => expect(onRecalculate).toHaveBeenCalledWith(container, "stable"));
  expect(onRecalculate).toHaveBeenCalledTimes(1);
});


test("clicking the active goal does not recalculate", async () => {
  const onRecalculate = vi.fn(async () => undefined);
  renderWorkspace("high_fill", { onRecalculate });

  await userEvent.click(screen.getByRole("button", { name: /装载率优先/ }));

  expect(onRecalculate).not.toHaveBeenCalled();
});


test("shows balance warning with CTA when imbalanced and goal is not stable", async () => {
  const onRecalculate = vi.fn(async () => undefined);
  renderWorkspace("high_fill", { lengthImbalance: 20, onRecalculate });

  expect(screen.getByRole("alert")).toHaveTextContent(
    "前后重量偏差较大（20%），建议切换到「重心稳妥」目标",
  );

  await userEvent.click(screen.getByRole("button", { name: "立即切换" }));

  await waitFor(() => expect(onRecalculate).toHaveBeenCalledWith(container, "stable"));
});


test("hides balance warning for the stable goal even when imbalanced", () => {
  renderWorkspace("stable", { lengthImbalance: 20 });

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});


test("hides balance warning when balanced", () => {
  renderWorkspace("easy", { lengthImbalance: 8 });

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});


test("shows identical-layout notice when switching goals yields the same fingerprint", () => {
  const { rerenderWith } = renderWorkspace("high_fill", { fingerprint: "same-geometry" });

  expect(screen.queryByTestId("identical-layout-notice")).not.toBeInTheDocument();

  rerenderWith("stable", "same-geometry");

  expect(screen.getByTestId("identical-layout-notice")).toHaveTextContent(
    "「重心稳妥」与「装载率优先」的装载布局几何相同（仅整体平移）",
  );
});


test("hides identical-layout notice when fingerprints differ", () => {
  const { rerenderWith } = renderWorkspace("high_fill", { fingerprint: "geometry-a" });

  rerenderWith("easy", "geometry-b");

  expect(screen.queryByTestId("identical-layout-notice")).not.toBeInTheDocument();
});


test("hides identical-layout notice on initial render and same-goal recalculation", () => {
  const { rerenderWith } = renderWorkspace("stable", { fingerprint: "geometry-a" });

  // 初始渲染不显示
  expect(screen.queryByTestId("identical-layout-notice")).not.toBeInTheDocument();

  // 同目标重算（request_id 变化但目标不变、指纹相同）也不显示
  rerenderWith("stable", "geometry-a");

  expect(screen.queryByTestId("identical-layout-notice")).not.toBeInTheDocument();
});
