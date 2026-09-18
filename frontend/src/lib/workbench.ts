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

// Relocation describes the final layout, not a physical path through intervening boxes.
export function relocateDraft(placements: Placement[], id: string, position: [number, number], wholeCargo: boolean, locked: Set<string>, container: ContainerSpec, gap = 0): { placements: Placement[]; error: string | null } {
  const selected = placements.find(p => p.id === id);
  const fail = (error: string) => ({ placements, error });
  if (!selected || locked.has(selected.cargo_id)) return fail('该货物已锁定，不能搬移');
  if (!position.every(Number.isFinite)) return fail('请输入有效坐标');
  const bounded = constrainMove(placements, id, [position[0], position[1], selected.z_mm], wholeCargo, locked, container).find(p => p.id === id)!;
  if (bounded.x_mm === selected.x_mm && bounded.y_mm === selected.y_mm) return { placements, error: null };
  const moving = new Set(placements.filter(p => wholeCargo ? p.cargo_id === selected.cargo_id : p.id === id).map(p => p.id));
  const floor = container.clearance_mm ?? 0;
  const overlaps = (a: Placement, b: Placement) => a.x_mm < b.x_mm + b.length_mm && b.x_mm < a.x_mm + a.length_mm
    && a.y_mm < b.y_mm + b.width_mm && b.y_mm < a.y_mm + a.width_mm;
  const settled: Placement[] = [];
  // Settle the remaining stack bottom-up, including cargo that lost its support.
  for (const p of placements.filter(p => !moving.has(p.id)).sort((a, b) => a.z_mm - b.z_mm)) {
    const z = Math.max(floor, ...settled.filter(b => overlaps(p, b)).map(b => b.z_mm + b.height_mm));
    if (locked.has(p.cargo_id) && z !== p.z_mm) return fail('搬移会改变已锁定货物的支撑，请先解锁相关 SKU');
    settled.push({ ...p, z_mm: z });
  }
  const target = settled.filter(p => p.cargo_id === selected.cargo_id && p.rotation === selected.rotation
    && p.length_mm === selected.length_mm && p.width_mm === selected.width_mm
    && Math.abs(p.x_mm - position[0]) <= Math.min(150, selected.length_mm / 4)
    && Math.abs(p.y_mm - position[1]) <= Math.min(150, selected.width_mm / 4))
    .sort((a, b) => Math.hypot(a.x_mm - position[0], a.y_mm - position[1]) - Math.hypot(b.x_mm - position[0], b.y_mm - position[1]))[0];
  const [x, y] = target ? [target.x_mm, target.y_mm] : position;
  const group = constrainMove(placements, id, [x, y, selected.z_mm], wholeCargo, locked, container).filter(p => moving.has(p.id));
  const destination = group.find(p => p.id === id)!;
  if (destination.x_mm === selected.x_mm && destination.y_mm === selected.y_mm) return { placements, error: null };
  // A SKU group moves rigidly; all members share the same vertical displacement.
  const dz = Math.max(...group.map(p => Math.max(floor, ...settled.filter(b => overlaps(p, b)).map(b => b.z_mm + b.height_mm)) - p.z_mm));
  const byId = new Map([...settled, ...group.map(p => ({ ...p, z_mm: p.z_mm + dz }))].map(p => [p.id, p]));
  const next = placements.map(p => byId.get(p.id)!);
  const error = geometryError(next, container, gap);
  return error ? fail(error) : { placements: next, error: null };
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
