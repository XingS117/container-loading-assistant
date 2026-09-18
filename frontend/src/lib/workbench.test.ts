import { createCargo } from './cargo';
import { moveDraft, rotateDraft, editHistory } from './workbench';
import type { Placement } from '../types';

const a: Placement = { id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 100, y_mm: 100, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, rotation: 'LWH', weight_g: 18000, step: 1 };
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
