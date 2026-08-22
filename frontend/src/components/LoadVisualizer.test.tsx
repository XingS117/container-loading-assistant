import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LoadVisualizer } from "./LoadVisualizer";
import type { CargoInput, ContainerSpec, PackingSolution } from "../types";


const container: ContainerSpec = {
  id: "demo",
  name: "演示柜",
  inner_length_mm: 2000,
  inner_width_mm: 1000,
  inner_height_mm: 1000,
  door_width_mm: 1000,
  door_height_mm: 1000,
  max_payload_g: 1000000,
  clearance_mm: 0,
};

const cargoItems: CargoInput[] = [
  {
    id: "a",
    sku: "A-01",
    name: "纸箱",
    kind: "carton",
    length_cm: 100,
    width_cm: 100,
    height_cm: 50,
    weight_kg: 10,
    quantity: 2,
    orientation_mode: "upright",
    stackable: true,
    max_layers: 2,
    max_top_load_kg: 10,
    fragile: false,
    must_load: false,
    unload_order: 0,
  },
];

const solution: PackingSolution = {
  profile: "high_fill",
  name: "装得多",
  placements: [
    { id: "a-0", cargo_id: "a", instance_index: 0, x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 1000, width_mm: 1000, height_mm: 500, rotation: "LWH", weight_g: 10000, step: 1 },
    { id: "a-1", cargo_id: "a", instance_index: 1, x_mm: 0, y_mm: 0, z_mm: 500, length_mm: 1000, width_mm: 1000, height_mm: 500, rotation: "LWH", weight_g: 10000, step: 2 },
  ],
  loaded_counts: { a: 2 },
  unloaded_counts: { a: 0 },
  metrics: {
    loaded_pieces: 2,
    loaded_weight_g: 20000,
    volume_utilization_pct: 50,
    weight_utilization_pct: 2,
    center_of_gravity: { x_mm: 500, y_mm: 500, z_mm: 500 },
    length_imbalance_pct: 0,
    width_imbalance_pct: 0,
    weight_imbalance_pct: 50,
    loading_steps: 2,
    cargo_zones: 2,
  },
  zones: [],
  pros: [],
  cons: [],
  warnings: [],
  identical_to: null,
};


test("switches from 3d to top view using the same placements", async () => {
  render(<LoadVisualizer container={container} solution={solution} cargoItems={cargoItems} />);

  await userEvent.click(screen.getByRole("button", { name: "俯视" }));

  expect(screen.getByTestId("layout-svg")).toHaveAttribute("data-visible-count", "2");
  expect(screen.getAllByText("A-01").length).toBeGreaterThan(0);
});


test("filters layout by loading step and layer", async () => {
  render(<LoadVisualizer container={container} solution={solution} cargoItems={cargoItems} />);
  await userEvent.click(screen.getByRole("button", { name: "俯视" }));

  const stepSlider = screen.getByRole("slider", { name: "装载步骤" });
  fireEvent.change(stepSlider, { target: { value: "1" } });
  expect(screen.getByTestId("layout-svg")).toHaveAttribute("data-visible-count", "1");

  await userEvent.click(screen.getByRole("button", { name: "分层" }));
  expect(screen.getByRole("slider", { name: "查看层高" })).toBeInTheDocument();
});


test("renders numbered zone outlines in the top view", async () => {
  render(<LoadVisualizer container={container} solution={{ ...solution, zones: [
    { step: 1, cargo_id: "a", x_mm: 0, y_mm: 0, length_mm: 1000, width_mm: 1000, piece_count: 2 },
  ] }} cargoItems={cargoItems} />);

  await userEvent.click(screen.getByRole("button", { name: "俯视" }));

  const zones = screen.getAllByTestId("layout-zone");
  expect(zones).toHaveLength(1);
  expect(zones[0]).toHaveTextContent("1");
  expect(zones[0]).toHaveTextContent("A-01 ×2");
});


test("falls back to the top view when WebGL is unavailable", async () => {
  render(<LoadVisualizer container={container} solution={solution} cargoItems={cargoItems} />);

  expect(await screen.findByRole("status")).toHaveTextContent("当前设备不支持 3D");
  expect(screen.getByRole("button", { name: "俯视" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByTestId("layout-svg")).toBeInTheDocument();
});
