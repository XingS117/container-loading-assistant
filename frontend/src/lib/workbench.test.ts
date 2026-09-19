import { createCargo } from './cargo';
import { moveDraft, rotateDraft, editHistory, constrainMove, geometryError, relocateDraft, removeDraft, swapDraft } from './workbench';
import type { ContainerSpec, Placement } from '../types';

const a: Placement = { id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 100, y_mm: 100, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, rotation: 'LWH', weight_g: 18000, step: 1 };
const box = { inner_length_mm: 3000, inner_width_mm: 2000, inner_height_mm: 2000, clearance_mm: 0 } as ContainerSpec;

test('removes a single sparse instance, settles its stack and can undo the whole change', () => {
  const top = {...a, id:'a-8', instance_index:8, z_mm:400};
  const list = [a, top];
  const result = removeDraft(list, a.id, new Set(), box);
  expect(result.error).toBeNull();
  expect(result.placements).toEqual([{...top, z_mm:0}]);
  expect(list[1].z_mm).toBe(400);
  const history = editHistory({past:[],present:list,future:[]}, {type:'edit',placements:result.placements});
  expect(editHistory(history, {type:'undo'}).present).toEqual(list);
  expect(removeDraft([a], a.id, new Set(), box).placements).toEqual([]);
});

test('removal cannot move locked supporters or leave partial support', () => {
  const top = {...a, id:'top', cargo_id:'b', z_mm:400};
  const list = [a, top];
  expect(removeDraft(list, a.id, new Set(['a']), box).placements).toBe(list);
  expect(removeDraft(list, a.id, new Set(['b']), box).error).toContain('锁定');
  const half = {...a, length_mm:300};
  const bridge = [half, {...half,id:'right',x_mm:400}, top];
  const failed = removeDraft(bridge, a.id, new Set(), box);
  expect(failed.error).toContain('支撑');
  expect(failed.placements).toBe(bridge);
});

test('swaps exact instance positions with sparse indices preserving identity, size and rotation', () => {
  const other = {...a,id:'b-7',cargo_id:'b',instance_index:7,x_mm:1800,y_mm:900,rotation:'WLH' as const,length_mm:400,width_mm:600};
  const result = swapDraft([a,other], a.id, other.id, new Set(), box, 20);
  expect(result.error).toBeNull();
  expect(result.placements).toEqual([{...a,x_mm:1800,y_mm:900}, {...other,x_mm:100,y_mm:100}]);
  expect(swapDraft([a,{...other,cargo_id:'a'}], a.id, other.id, new Set(), box).error).toBeNull();
});

test('swap rejects locks, identical selections, boundary, collision and lost support atomically', () => {
  const other = {...a,id:'b',cargo_id:'b',x_mm:2600,length_mm:400};
  const list = [a,other];
  expect(swapDraft(list,a.id,other.id,new Set(['b']),box).error).toContain('锁定');
  expect(swapDraft(list,a.id,a.id,new Set(),box).error).toBeTruthy();
  expect(swapDraft(list,a.id,other.id,new Set(),box).placements).toBe(list);
  const short = {...a,id:'short',x_mm:1800,height_mm:200};
  const stack = [a,{...a,id:'top',z_mm:400},short];
  expect(swapDraft(stack,a.id,short.id,new Set(),box).error).toContain('支撑');
  const small = {...a,id:'small',x_mm:1000,length_mm:300};
  const neighbor = {...a,id:'neighbor',x_mm:1400};
  expect(swapDraft([a,small,neighbor],a.id,small.id,new Set(),box).error).toContain('重叠');
});
test('snaps to all cabinet walls while respecting clearance', () => {
  const cabinet = { ...box, clearance_mm: 20 };
  const p = { ...a, z_mm: 20 };
  expect(relocateDraft([p], p.id, [45, 49], false, new Set(), cabinet).placements[0]).toMatchObject({ x_mm: 20, y_mm: 20, z_mm: 20 });
  expect(relocateDraft([p], p.id, [2355, 1550], false, new Set(), cabinet).placements[0]).toMatchObject({ x_mm: 2380, y_mm: 1580 });
});
test('snaps beside different cargo with the configured gap and aligns its edge', () => {
  const neighbor = { ...a, id: 'b', cargo_id: 'b', x_mm: 1400, y_mm: 600 };
  const result = relocateDraft([a, neighbor], a.id, [755, 625], false, new Set(), box, 20);
  expect(result.error).toBeNull();
  expect(result.placements[0]).toMatchObject({ x_mm: 780, y_mm: 600, z_mm: 0 });
  expect(result.snapped).toBe(true);
  expect(result.placements[1]).toEqual(neighbor);
  expect(relocateDraft([a, neighbor], a.id, [2025, 615], false, new Set(), box).placements[0]).toMatchObject({ x_mm: 2000, y_mm: 600 });
  expect(relocateDraft([a, neighbor], a.id, [1420, 175], false, new Set(), box).placements[0]).toMatchObject({ x_mm: 1400, y_mm: 200 });
  expect(relocateDraft([a, neighbor], a.id, [1410, 1040], false, new Set(), box).placements[0]).toMatchObject({ x_mm: 1400, y_mm: 1000 });
});
test('keeps intentional gaps and ignores distant alignment targets', () => {
  expect(relocateDraft([a], a.id, [150, 160], false, new Set(), box).placements[0]).toMatchObject({ x_mm: 150, y_mm: 160 });
  const distant = { ...a, id: 'b', cargo_id: 'b', x_mm: 1400, y_mm: 1500 };
  expect(relocateDraft([a, distant], a.id, [1420, 200], false, new Set(), box).placements[0]).toMatchObject({ x_mm: 1420, y_mm: 200 });
});
test('snaps the outer edge of a whole group without changing its relative positions', () => {
  const group = [a, { ...a, id: 'a-1', x_mm: 1000 }];
  const result = relocateDraft(group, a.id, [1470, 25], true, new Set(), box);
  expect(result.error).toBeNull();
  expect(result.placements.map(p => [p.x_mm, p.y_mm])).toEqual([[1500, 0], [2400, 0]]);
});
test('does not snap an upper box away from complete support to a nearby wall', () => {
  const support = { ...a, id: 'support', x_mm: 30, y_mm: 1000 };
  const result = relocateDraft([a, support], a.id, [30, 1000], false, new Set(), box);
  expect(result.error).toBeNull();
  expect(result.placements[0]).toMatchObject({ x_mm: 30, y_mm: 1000, z_mm: 400 });
});
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
