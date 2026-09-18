import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LayoutWorkbench } from './LayoutWorkbench';
import { reviewLayout } from '../lib/api';
import { createCargo } from '../lib/cargo';
import type { ContainerSpec, PackingSolution } from '../types';

vi.mock('../lib/api', () => ({ reviewLayout: vi.fn() }));
vi.mock('./LoadVisualizer', () => ({ LoadVisualizer: () => <div>画布</div> }));
const cargo = { ...createCargo('A'), id: 'a' };
const container = { id: 'c', inner_length_mm: 3000, inner_width_mm: 2000, inner_height_mm: 2000 } as ContainerSpec;
const solution = { profile: 'easy', name: '易操作', placements: [{ id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, weight_g: 18000, rotation: 'LWH', step: 1 }], metrics: { loaded_pieces: 1, length_imbalance_pct: 80, width_imbalance_pct: 80 }, zones: [], warnings: [], pros: [], cons: [] } as unknown as PackingSolution;
const valid = { valid: true, errors: [], metrics: solution.metrics, zones: [] };
beforeEach(() => { vi.mocked(reviewLayout).mockReset().mockResolvedValue(valid); });

test('edits any profile, reviews exact draft and applies authoritative metrics', async () => {
  const apply = vi.fn();
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={2} onApply={apply} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('X 柜长 cm'), { target: { value: '50' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  expect(screen.getByRole('button', { name: '撤销' })).toBeEnabled();
  await waitFor(() => expect(reviewLayout).toHaveBeenCalledWith(container, [cargo], [expect.objectContaining({ x_mm: 500 })], 2));
  await userEvent.click(screen.getByRole('button', { name: '应用调整' }));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ profile: 'easy', placements: [expect.objectContaining({ x_mm: 500 })], metrics: valid.metrics }), expect.any(Set));
});

test('shows cargo dimensions as read-only while allowing position edits', async () => {
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  expect(screen.getByLabelText('货物尺寸（不可修改）')).toHaveValue('60 × 40 × 40 cm');
  expect(screen.getByLabelText('货物尺寸（不可修改）')).toHaveAttribute('readonly');
});

test('invalid draft cannot be applied and undo restores coordinates', async () => {
  vi.mocked(reviewLayout).mockResolvedValue({ valid: false, errors: [{ code: 'UNSUPPORTED', message: '缺少支撑', placement_ids: ['a-0'] }], metrics: null, zones: [] });
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('Z 高度 cm'), { target: { value: '50' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  expect(await screen.findByText('缺少支撑')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '应用调整' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '撤销' }));
  expect(screen.getByLabelText('Z 高度 cm')).toHaveValue(0);
});

test('ignores a stale review and waits for the current draft review', async () => {
  const resolvers: Array<(value: typeof valid) => void> = [];
  vi.mocked(reviewLayout).mockImplementation(() => new Promise(resolve => { resolvers.push(resolve); }));
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await waitFor(() => expect(resolvers).toHaveLength(1));
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('X 柜长 cm'), { target: { value: '40' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  await waitFor(() => expect(resolvers).toHaveLength(2));
  await act(async () => { resolvers[0](valid); });
  expect(screen.getByRole('button', { name: '应用调整' })).toBeDisabled();
  await act(async () => { resolvers[1](valid); });
  expect(screen.getByRole('button', { name: '应用调整' })).toBeEnabled();
});
