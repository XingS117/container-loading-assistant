import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CargoTable } from "./CargoTable";
import { createCargo } from "../lib/cargo";


test("adds and removes cargo rows", async () => {
  const onChange = vi.fn();
  const rows = [createCargo("A-001")];
  const { rerender } = render(<CargoTable rows={rows} onChange={onChange} />);

  await userEvent.click(screen.getByRole("button", { name: "添加货物" }));
  expect(onChange).toHaveBeenCalledWith(expect.arrayContaining([rows[0], expect.any(Object)]));

  rerender(<CargoTable rows={rows} onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: "删除 A-001" }));
  expect(onChange).toHaveBeenCalledWith([]);
});

test("allows replacing a numeric value after clearing the field", async () => {
  const onChange = vi.fn();
  const rows = [createCargo("A-001")];
  render(<CargoTable rows={rows} onChange={onChange} />);

  const weightInput = screen.getByRole("spinbutton", { name: "单重 A-001" });
  await userEvent.clear(weightInput);
  expect(weightInput).toHaveValue(null);

  await userEvent.type(weightInput, "12.5");
  expect(onChange).toHaveBeenLastCalledWith([
    expect.objectContaining({ weight_kg: 12.5 }),
  ]);
});
