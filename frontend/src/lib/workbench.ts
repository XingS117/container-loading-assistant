import type { CargoInput, Orientation, Placement } from "../types";

export interface EditHistory {
  past: Placement[][];
  present: Placement[];
  future: Placement[][];
}

export type EditAction =
  | { type: "edit"; placements: Placement[] }
  | { type: "undo" }
  | { type: "redo" };

const orientationDimensions = (cargo: CargoInput, orientation: Orientation): [number, number, number] => {
  const dimensions: Record<string, number> = { L: cargo.length_cm * 10, W: cargo.width_cm * 10, H: cargo.height_cm * 10 };
  return [dimensions[orientation[0]], dimensions[orientation[1]], dimensions[orientation[2]]];
};

const copyPlacements = (placements: Placement[]) => placements.map((placement) => ({ ...placement }));

export function moveDraft(
  placements: Placement[],
  placementId: string,
  position: [number, number, number],
  wholeCargo: boolean,
  lockedCargoIds: Set<string>,
): Placement[] {
  const selected = placements.find((placement) => placement.id === placementId);
  if (!selected || lockedCargoIds.has(selected.cargo_id)) return placements;
  const ids = new Set(wholeCargo ? placements.filter((item) => item.cargo_id === selected.cargo_id).map((item) => item.id) : [placementId]);
  const delta = [position[0] - selected.x_mm, position[1] - selected.y_mm, position[2] - selected.z_mm];
  return placements.map((placement) => ids.has(placement.id)
    ? { ...placement, x_mm: placement.x_mm + delta[0], y_mm: placement.y_mm + delta[1], z_mm: placement.z_mm + delta[2] }
    : { ...placement });
}

export function rotateDraft(
  placements: Placement[],
  cargoId: string,
  cargo: CargoInput,
  orientation: Orientation,
  lockedCargoIds: Set<string>,
): Placement[] {
  const allowed: Orientation[] = cargo.orientation_mode === "upright"
    ? ["LWH", "WLH"]
    : cargo.orientation_mode === "side"
      ? ["LWH", "WLH", "LHW", "WHL"]
      : ["LWH", "LHW", "WLH", "WHL", "HLW", "HWL"];
  if (lockedCargoIds.has(cargoId) || !allowed.includes(orientation)) return placements;
  return placements.map((placement) => {
    if (placement.cargo_id !== cargoId) return { ...placement };
    const [length, width, height] = orientationDimensions(cargo, orientation);
    return {
      ...placement,
      x_mm: Math.round(placement.x_mm + (placement.length_mm - length) / 2),
      y_mm: Math.round(placement.y_mm + (placement.width_mm - width) / 2),
      length_mm: length,
      width_mm: width,
      height_mm: height,
      rotation: orientation,
    };
  });
}

export function editHistory(history: EditHistory, action: EditAction): EditHistory {
  if (action.type === "undo") {
    const previous = history.past.at(-1);
    return previous ? { past: history.past.slice(0, -1), present: copyPlacements(previous), future: [copyPlacements(history.present), ...history.future] } : history;
  }
  if (action.type === "redo") {
    const next = history.future[0];
    return next ? { past: [...history.past, copyPlacements(history.present)], present: copyPlacements(next), future: history.future.slice(1) } : history;
  }
  return { past: [...history.past, copyPlacements(history.present)], present: copyPlacements(action.placements), future: [] };
}
