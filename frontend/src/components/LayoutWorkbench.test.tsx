import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LayoutWorkbench } from './LayoutWorkbench';
import { reviewLayout } from '../lib/api';
import { createCargo } from '../lib/cargo';
import type { ContainerSpec, PackingSolution } from '../types';

vi.mock('../lib/api', () => ({ reviewLayout: vi.fn() }));
vi.mock('./LoadVisualizer', () => ({ LoadVisualizer: ({ onMovePlacement }: { onMovePlacement?: (id: string, x: number, y: number) => void }) => <button onClick={() => onMovePlacement?.('a-0', 1400, 0)}>模拟搬移</button> }));
const cargo = { ...createCargo('A'), id: 'a' };
const container = { id: 'c', inner_length_mm: 3000, inner_width_mm: 2000, inner_height_mm: 2000 } as ContainerSpec;
const solution = { profile: 'easy', name: '易操作', placements: [{ id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, weight_g: 18000, rotation: 'LWH', step: 1 }], metrics: { loaded_pieces: 1, length_imbalance_pct: 80, width_imbalance_pct: 80 }, zones: [], warnings: [], pros: [], cons: [] } as unknown as PackingSolution;
const valid = { valid: true, errors: [], metrics: solution.metrics, zones: [] };
beforeEach(() => { vi.mocked(reviewLayout).mockReset().mockResolvedValue(valid); });

test('removes only the selected piece, settles above, shows unloaded counts and undoes', async () => {
  const stack = {...solution, placements:[...solution.placements, {...solution.placements[0],id:'a-1',instance_index:1,z_mm:400}]};
  render(<LayoutWorkbench solution={stack} container={container} cargoItems={[{...cargo,quantity:2}]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', {name:'A · 第 1 件'}));
  await userEvent.click(screen.getByLabelText('移动同 SKU 全部货物'));
  await userEvent.click(screen.getByRole('button', {name:'移出选中单件'}));
  await waitFor(() => expect(screen.queryByRole('button', {name:'A · 第 1 件'})).not.toBeInTheDocument());
  expect(screen.getByText('A：未装 1 / 2 件')).toBeInTheDocument();
  expect(reviewLayout).toHaveBeenCalledWith(container, [{...cargo,quantity:2}], [expect.objectContaining({id:'a-1',z_mm:0})], 0);
  await userEvent.click(screen.getByRole('button', {name:'撤销'}));
  await userEvent.click(screen.getByRole('button', {name:'A · 第 2 件'}));
  expect(screen.getByLabelText('Z 高度 cm')).toHaveValue(40);
  expect(screen.queryByText('A：未装 1 / 2 件')).not.toBeInTheDocument();
});

test('cannot remove must-load or locked cargo, and server rejection keeps the piece', async () => {
  const props = {solution,container,cargoItems:[{...cargo,must_load:true}],itemGapCm:0,onApply:vi.fn(),onClose:() => {}};
  const {rerender} = render(<LayoutWorkbench {...props} />);
  await userEvent.click(screen.getByRole('button', {name:'A · 第 1 件'}));
  expect(screen.getByRole('button', {name:'移出选中单件'})).toBeDisabled();
  rerender(<LayoutWorkbench {...props} cargoItems={[cargo]} />);
  await userEvent.click(screen.getByRole('button', {name:'锁定该 SKU'}));
  expect(screen.getByRole('button', {name:'移出选中单件'})).toBeDisabled();
  await userEvent.click(screen.getByRole('button', {name:'解锁该 SKU'}));
  vi.mocked(reviewLayout).mockResolvedValue({...valid,valid:false,errors:[{code:'TOP_LOAD_EXCEEDED',message:'落位后承重超限',placement_ids:[]}],metrics:null});
  await userEvent.click(screen.getByRole('button', {name:'移出选中单件'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('落位后承重超限');
  expect(screen.getByRole('button', {name:'A · 第 1 件'})).toBeInTheDocument();
  expect(screen.getByRole('button', {name:'撤销'})).toBeDisabled();
});

test('swaps two sparse instances and leaves the original positions when server rejects', async () => {
  const other = {...cargo,id:'b',sku:'B'};
  const second = {...solution.placements[0],id:'b-7',cargo_id:'b',instance_index:7,x_mm:1400};
  const pair = {...solution,placements:[solution.placements[0],second]};
  render(<LayoutWorkbench solution={pair} container={container} cargoItems={[cargo,other]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', {name:'A · 第 1 件'}));
  await userEvent.selectOptions(screen.getByLabelText('交换对象'), 'b-7');
  await userEvent.click(screen.getByRole('button', {name:'交换两件位置'}));
  await waitFor(() => expect(screen.getByLabelText('X 柜长 cm')).toHaveValue(140));
  expect(reviewLayout).toHaveBeenCalledWith(container,[cargo,other],[expect.objectContaining({id:'a-0',x_mm:1400}),expect.objectContaining({id:'b-7',x_mm:0})],0);
  vi.mocked(reviewLayout).mockResolvedValue({...valid,valid:false,errors:[{code:'TOP_LOAD_EXCEEDED',message:'承重超限',placement_ids:[]}],metrics:null});
  await userEvent.click(screen.getByRole('button', {name:'交换两件位置'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('承重超限');
  expect(screen.getByLabelText('X 柜长 cm')).toHaveValue(140);
});

test('applying an empty layout recomputes loaded/unloaded counts without changing the order', async () => {
  const apply = vi.fn();
  const order = [{...cargo,quantity:1}];
  vi.mocked(reviewLayout).mockImplementation(async (_container,_cargo,draft) => ({...valid,placements:draft,metrics:{...solution.metrics,loaded_pieces:draft.length}}));
  render(<LayoutWorkbench solution={{...solution,loaded_counts:{a:1},unloaded_counts:{a:0}}} container={container} cargoItems={order} itemGapCm={0} onApply={apply} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', {name:'A · 第 1 件'}));
  await userEvent.click(screen.getByRole('button', {name:'移出选中单件'}));
  await waitFor(() => expect(screen.getByRole('button', {name:'应用调整'})).toBeEnabled());
  await userEvent.click(screen.getByRole('button', {name:'应用调整'}));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({placements:[],loaded_counts:{a:0},unloaded_counts:{a:1},metrics:expect.objectContaining({loaded_pieces:0}),warnings:expect.arrayContaining([expect.stringContaining('未装 1 件')])}),expect.any(Set));
  expect(order[0].quantity).toBe(1);
});

test('relocates a bottom box, settles its upper box and undoes the complete operation', async () => {
  const stack = { ...solution, placements: [...solution.placements, { ...solution.placements[0], id: 'a-1', instance_index: 1, z_mm: 400 }] };
  render(<LayoutWorkbench solution={stack} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  await userEvent.click(screen.getByRole('button', { name: '移动货物' }));
  await userEvent.click(screen.getByRole('button', { name: '模拟搬移' }));
  await waitFor(() => expect(screen.getByLabelText('X 柜长 cm')).toHaveValue(140));
  expect(reviewLayout).toHaveBeenCalledWith(container, [cargo], [expect.objectContaining({ x_mm: 1400, z_mm: 0 }), expect.objectContaining({ x_mm: 0, z_mm: 0 })], 0);
  expect(screen.getByText(/原货位 1 件货物已向下归位/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '撤销' }));
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 2 件' }));
  expect(screen.getByLabelText('Z 高度 cm')).toHaveValue(40);
});

test('edits any profile, reviews exact draft and applies authoritative metrics', async () => {
  const apply = vi.fn();
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={2} onApply={apply} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('X 柜长 cm'), { target: { value: '50' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '撤销' })).toBeEnabled());
  await waitFor(() => expect(reviewLayout).toHaveBeenCalledWith(container, [cargo], [expect.objectContaining({ x_mm: 500 })], 2));
  await waitFor(() => expect(screen.getByRole('button', { name: '应用调整' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '应用调整' }));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ profile: 'easy', placements: [expect.objectContaining({ x_mm: 500 })], metrics: valid.metrics }), expect.any(Set));
});

test('shows cargo dimensions as read-only while allowing position edits', async () => {
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  expect(screen.getByLabelText('货物尺寸（不可修改）')).toHaveValue('60 × 40 × 40 cm');
  expect(screen.getByLabelText('货物尺寸（不可修改）')).toHaveAttribute('readonly');
});

test('applies regenerated server steps instead of stale draft steps', async () => {
  const apply = vi.fn();
  vi.mocked(reviewLayout).mockImplementation(async (_container, _cargo, draft) => ({
    ...valid, placements: draft.map(p => ({ ...p, step: 7 })),
  }));
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={apply} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('X 柜长 cm'), { target: { value: '50' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '应用调整' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '应用调整' }));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ placements: [expect.objectContaining({ x_mm: 500, step: 7 })] }), expect.any(Set));
});

test('floating moves are rejected before changing the layout', async () => {
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  fireEvent.change(screen.getByLabelText('Z 高度 cm'), { target: { value: '50' } });
  await userEvent.click(screen.getByRole('button', { name: '预览坐标' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('支撑');
  expect(screen.getByRole('button', { name: '应用调整' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled();
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
  await waitFor(() => expect(resolvers).toHaveLength(3));
  await act(async () => { resolvers[2](valid); });
  await waitFor(() => expect(screen.getByRole('button', { name: '应用调整' })).toBeEnabled());
});

test('step and rotation buttons preserve dimensions and server rejection preserves position', async () => {
  render(<LayoutWorkbench solution={solution} container={container} cargoItems={[cargo]} itemGapCm={0} onApply={vi.fn()} onClose={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'A · 第 1 件' }));
  await userEvent.click(screen.getByRole('button', { name: '向柜门移一件' }));
  await waitFor(() => expect(screen.getByLabelText('X 柜长 cm')).toHaveValue(60));
  await userEvent.click(screen.getByRole('button', { name: '水平旋转 90°' }));
  await waitFor(() => expect(screen.getByLabelText('当前单件朝向')).toHaveValue('WLH'));
  expect(screen.getByLabelText('货物尺寸（不可修改）')).toHaveValue('60 × 40 × 40 cm');
  vi.mocked(reviewLayout).mockResolvedValue({ valid: false, errors: [{ code: 'TOP_LOAD_EXCEEDED', message: '承重超限', placement_ids: ['a-0'] }], metrics: null, zones: [] });
  await userEvent.click(screen.getByRole('button', { name: '向柜门移一件' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('承重超限');
  expect(screen.getByLabelText('X 柜长 cm')).toHaveValue(60);
});
