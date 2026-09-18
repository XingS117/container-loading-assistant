import type { CargoInput, ContainerSpec, Orientation, Placement } from "../types";

// Fast geometric feedback during dragging; the server still checks load and cargo rules.
export function geometryError(placements: Placement[], container: ContainerSpec, gap = 0): string | null {
  const c = container.clearance_mm ?? 0;
  const supportedArea = new Map<string, number>();
  for (const p of placements) {
    if (![p.x_mm, p.y_mm, p.z_mm].every(Number.isFinite) || p.x_mm < c || p.y_mm < c || p.z_mm < c
      || p.x_mm + p.length_mm > container.inner_length_mm - c
      || p.y_mm + p.width_mm > container.inner_width_mm - c
      || p.z_mm + p.height_mm > container.inner_height_mm - c) return '超出柜体有效边界，已保留原位置';
  }
  const ordered = [...placements].sort((a, b) => a.x_mm - b.x_mm);
  for (let i = 0; i < ordered.length; i++) {
    const a = ordered[i];
    for (let j = i + 1; j < ordered.length; j++) {
      const b = ordered[j];
      if (b.x_mm >= a.x_mm + a.length_mm + gap) break;
      const dx = Math.min(a.x_mm + a.length_mm, b.x_mm + b.length_mm) - Math.max(a.x_mm, b.x_mm);
      const dy = Math.min(a.y_mm + a.width_mm, b.y_mm + b.width_mm) - Math.max(a.y_mm, b.y_mm);
      const sameHeight = a.z_mm < b.z_mm + b.height_mm && b.z_mm < a.z_mm + a.height_mm;
      if (sameHeight && dx > 0 && dy > 0) return '与其他货物重叠，已保留原位置';
      if (sameHeight && Math.hypot(Math.max(0, -dx), Math.max(0, -dy)) < gap) return '货物间隙不足，已保留原位置';
      if (dx > 0 && dy > 0) {
        if (a.z_mm + a.height_mm === b.z_mm) supportedArea.set(b.id, (supportedArea.get(b.id) ?? 0) + dx * dy);
        if (b.z_mm + b.height_mm === a.z_mm) supportedArea.set(a.id, (supportedArea.get(a.id) ?? 0) + dx * dy);
      }
    }
  }
  if (placements.some(p => p.z_mm > c && (supportedArea.get(p.id) ?? 0) < p.length_mm * p.width_mm)) return '下层没有完整支撑，或移动会抽走上层支撑，已保留原位置';
  return null;
}

export function constrainMove(placements: Placement[], id: string, position: [number, number, number], wholeCargo: boolean, locked: Set<string>, container: ContainerSpec): Placement[] {
  const p = placements.find(item => item.id === id);
  if (!p || locked.has(p.cargo_id)) return placements;
  const group = placements.filter(item => wholeCargo ? item.cargo_id === p.cargo_id : item.id === id);
  const c = container.clearance_mm ?? 0;
  const axes = ['x_mm', 'y_mm', 'z_mm'] as const;
  const sizes = ['length_mm', 'width_mm', 'height_mm'] as const;
  const limits = [container.inner_length_mm, container.inner_width_mm, container.inner_height_mm];
  const bounded = position.map((value, i) => {
    const low = Math.max(...group.map(item => c - item[axes[i]]));
    const high = Math.min(...group.map(item => limits[i] - c - item[axes[i]] - item[sizes[i]]));
    return p[axes[i]] + Math.max(low, Math.min(high, Math.round(value) - p[axes[i]]));
  }) as [number, number, number];
  return moveDraft(placements, id, bounded, wholeCargo, locked);
}

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
