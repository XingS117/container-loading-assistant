import { createCargo } from './cargo';
import { moveDraft, rotateDraft, editHistory, constrainMove, geometryError, relocateDraft } from './workbench';
import type { ContainerSpec, Placement } from '../types';

const a: Placement = { id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 100, y_mm: 100, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, rotation: 'LWH', weight_g: 18000, step: 1 };
const box = { inner_length_mm: 3000, inner_width_mm: 2000, inner_height_mm: 2000, clearance_mm: 0 } as ContainerSpec;
test('relocates an upper box to empty floor and settles the stack after extracting its bottom', () => {
  const stack = [a, { ...a, id: 'top', z_mm: 400 }];
  const upper = relocateDraft(stack, 'top', [1400, 100], false, new Set(), box);
  expect(upper.error).toBeNull();
  expect(upper.placements[1]).toMatchObject({ x_mm: 1400, z_mm: 0 });
  const bottom = relocateDraft(stack, a.id, [1400, 100], false, new Set(), box);
  expect(bottom.error).toBeNull();
  expect(bottom.placements[0]).toMatchObject({ x_mm: 1400, z_mm: 0 });
  expect(bottom.placements[1]).toMatchObject({ x_mm: 100, z_mm: 0 });
  expect(stack[1].z_mm).toBe(400);
});
test('snaps near matching cargo and places above its full support', () => {
  const target = { ...a, id: 'target', x_mm: 1400 };
  const result = relocateDraft([a, target], a.id, [1440, 120], false, new Set(), box);
  expect(result.error).toBeNull();
  expect(result.placements[0]).toMatchObject({ x_mm: 1400, y_mm: 100, z_mm: 400, length_mm: 600, width_mm: 400 });
});
test('rejects partial support and settling locked cargo, clamps to walls', () => {
  const partial = relocateDraft([a, { ...a, id: 'target', x_mm: 1400 }], a.id, [1750, 100], false, new Set(), box);
  expect(partial.error).toContain('支撑');
  const locked = relocateDraft([a, { ...a, id: 'top', cargo_id: 'locked', z_mm: 400 }], a.id, [1400, 100], false, new Set(['locked']), box);
  expect(locked.error).toContain('锁定');
  const clamped = relocateDraft([a], a.id, [99999, -100], false, new Set(), box);
  expect(clamped.placements[0]).toMatchObject({ x_mm: 2400, y_mm: 0, z_mm: 0 });
});
test('keeps whole SKU stacks together and rejects moves without headroom', () => {
  const stack = [a, { ...a, id: 'top', z_mm: 400 }];
  expect(relocateDraft(stack, a.id, [a.x_mm, a.y_mm], false, new Set(), box).placements).toEqual(stack);
  const result = relocateDraft(stack, a.id, [1400, 100], true, new Set(), box);
  expect(result.error).toBeNull();
  expect(result.placements.map(p => [p.x_mm, p.z_mm])).toEqual([[1400, 0], [1400, 400]]);
  const ceiling = relocateDraft([a, { ...a, id: 'target', x_mm: 1400, height_mm: 1800 }], a.id, [1400, 100], false, new Set(), box);
  expect(ceiling.error).toContain('边界');
});
test('clamps the whole moving group inside the container without resizing', () => {
  const list = [a, { ...a, id: 'a-1', x_mm: 1000 }];
  const result = constrainMove(list, a.id, [10000, -20, 0], true, new Set(), box);
  expect(result[0]).toMatchObject({ x_mm: 1500, y_mm: 0, length_mm: 600, width_mm: 400 });
  expect(result[1].x_mm).toBe(2400);
});
test('rejects floating goods and removing the support of another box', () => {
  expect(geometryError([{ ...a, z_mm: 100 }], box)).toContain('支撑');
  const stack = [a, { ...a, id: 'a-1', z_mm: 400 }];
  expect(geometryError(stack, box)).toBeNull();
  expect(geometryError(moveDraft(stack, a.id, [1000, 100, 0], false, new Set()), box)).toContain('支撑');
});
test('accepts full support spanning two boxes but rejects collision and gaps', () => {
  const base = { ...a, length_mm: 300 };
  const stack = [base, { ...base, id: 'b', x_mm: 400 }, { ...a, id: 'top', z_mm: 400 }];
  expect(geometryError(stack, box)).toBeNull();
  expect(geometryError([a, { ...a, id: 'b', x_mm: 650 }], box)).toContain('重叠');
  expect(geometryError([a, { ...a, id: 'b', x_mm: 710 }], box, 20)).toContain('间隙');
});
test('moves one instance or the whole SKU without mutating the original', () => {
  const list = [a, { ...a, id: 'a-1', z_mm: 400 }];
  expect(moveDraft(list, a.id, [200, 100, 0], false, new Set())[1].x_mm).toBe(100);
  expect(moveDraft(list, a.id, [200, 100, 0], true, new Set())[1].x_mm).toBe(200);
  expect(a.x_mm).toBe(100);
  expect(moveDraft(list, a.id, [200, 100, 0], true, new Set(['a']))).toBe(list);
});
test('rotation uses allowed dimensions and keeps integer coordinates', () => {
  const cargo = { ...createCargo(), id: 'a' };
  expect(rotateDraft([a], a.cargo_id, cargo, 'WLH', new Set())[0]).toMatchObject({ length_mm: 400, width_mm: 600, rotation: 'WLH' });
  expect(rotateDraft([a], a.cargo_id, cargo, 'HLW', new Set())).toEqual([a]);
});
test('undo and redo preserve snapshots and a fresh edit clears redo', () => {
  const initial = { past: [] as Placement[][], present: [a], future: [] as Placement[][] };
  const moved = moveDraft([a], a.id, [200, 100, 0], false, new Set());
  const edited = editHistory(initial, { type: 'edit', placements: moved });
  const undone = editHistory(edited, { type: 'undo' });
  expect(undone.present).toEqual([a]);
  expect(editHistory(undone, { type: 'redo' }).present).toEqual(moved);
  expect(editHistory(undone, { type: 'edit', placements: moved }).future).toEqual([]);
});
