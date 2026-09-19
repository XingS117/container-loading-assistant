import { buildLoadingWorksheet, orientationLabel } from './loadingWorksheet';
import { createCargo } from './cargo';
import type { Placement } from '../types';

const cargo = { ...createCargo('A'), id: 'a', fragile: true };
const base: Placement = { id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 100, y_mm: 200, z_mm: 10, length_mm: 600, width_mm: 400, height_mm: 400, rotation: 'LWH', weight_g: 30000, step: 1 };

test('derives one worksheet row per actual placement without mutating the layout', () => {
  const placements = [base, { ...base, id: 'a-1', instance_index: 1, z_mm: 410 }];
  const original = structuredClone(placements);
  const rows = buildLoadingWorksheet(placements, [cargo], 10);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toMatchObject({ label: 'A · 第 1 件', positionCm: '10 / 20 / 1', sizeCm: '60 × 40 × 40', weightKg: 30, supportLabels: [] });
  expect(rows[0].notes.join(' ')).toContain('易碎');
  expect(rows[0].notes.join(' ')).toContain('重件');
  expect(rows[1].supportLabels).toEqual(['A · 第 1 件']);
  expect(placements).toEqual(original);
});

test('uses actual rotated dimensions and explains every orientation', () => {
  const row = buildLoadingWorksheet([{ ...base, rotation: 'WLH', length_mm: 400, width_mm: 600 }], [cargo], 10)[0];
  expect(row.sizeCm).toBe('40 × 60 × 40');
  expect(row.orientation).toBe('原宽沿柜长，原长沿柜宽，原高朝上');
  expect(orientationLabel('LHW')).toBe('原长沿柜长，原高沿柜宽，原宽朝上');
  expect(orientationLabel('WHL')).toBe('原宽沿柜长，原高沿柜宽，原长朝上');
  expect(orientationLabel('HLW')).toBe('原高沿柜长，原长沿柜宽，原宽朝上');
  expect(orientationLabel('HWL')).toBe('原高沿柜长，原宽沿柜宽，原长朝上');
});

test('orders same-step supports first and lists every bridging support', () => {
  const upper = { ...base, id: 'a-2', instance_index: 2, x_mm: 400, z_mm: 410 };
  const other = { ...base, id: 'a-1', instance_index: 1, x_mm: 700 };
  const rows = buildLoadingWorksheet([upper, other, base], [cargo], 10);
  expect(rows.map(r => r.placement.id)).toEqual(['a-0', 'a-1', 'a-2']);
  expect(rows[2].supportLabels).toEqual(['A · 第 1 件', 'A · 第 2 件']);
  expect(rows[2].issues).toEqual([]);
});

test('flags missing and late support instead of presenting them as executable', () => {
  const floating = { ...base, id: 'a-1', instance_index: 1, z_mm: 510 };
  expect(buildLoadingWorksheet([floating], [cargo], 10)[0].issues.join(' ')).toContain('支撑');
  const rows = buildLoadingWorksheet([{ ...floating, z_mm: 410 }, { ...base, step: 2 }], [cargo], 10);
  expect(rows[0].issues.join(' ')).toContain('顺序');
});

test('handles empty layouts and missing cargo data explicitly', () => {
  expect(buildLoadingWorksheet([], [cargo], 0)).toEqual([]);
  expect(buildLoadingWorksheet([base], [], 10)[0].issues.join(' ')).toContain('资料');
});
