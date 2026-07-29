import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ContainerPicker } from "./ContainerPicker";
import type { ContainerSpec } from "../types";


const presets: ContainerSpec[] = [
  {
    id: "20gp",
    name: "20GP",
    inner_length_mm: 5898,
    inner_width_mm: 2352,
    inner_height_mm: 2393,
    door_width_mm: 2340,
    door_height_mm: 2280,
    max_payload_g: 28200000,
    clearance_mm: 0,
  },
  {
    id: "40hq",
    name: "40HQ",
    inner_length_mm: 12032,
    inner_width_mm: 2352,
    inner_height_mm: 2698,
    door_width_mm: 2340,
    door_height_mm: 2585,
    max_payload_g: 28600000,
    clearance_mm: 0,
  },
];


test("selects a standard container", async () => {
  const onSelect = vi.fn();
  render(
    <ContainerPicker presets={presets} selected={presets[0]} onSelect={onSelect} />,
  );

  await userEvent.click(screen.getByRole("button", { name: /40HQ/ }));

  expect(onSelect).toHaveBeenCalledWith(presets[1]);
});
