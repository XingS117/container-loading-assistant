import { render, screen, within } from '@testing-library/react';
import { LoadingWorksheet } from './LoadingWorksheet';
import { createCargo } from '../lib/cargo';
import type { PackingSolution, Placement } from '../types';

const cargo = { ...createCargo('玻璃箱'), id: 'a', fragile: true };
const placement: Placement = { id: 'a-0', cargo_id: 'a', instance_index: 0, x_mm: 150, y_mm: 200, z_mm: 0, length_mm: 400, width_mm: 600, height_mm: 400, rotation: 'WLH', weight_g: 30000, step: 1 };
const solution = { name: '重心稳妥', placements: [placement] } as PackingSolution;

test('shows coordinates, orientation, weight and handling notes for every piece', () => {
  render(<LoadingWorksheet solution={solution} cargoItems={[cargo]} clearanceMm={0} />);
  expect(screen.getByRole('table', { name: '重心稳妥逐件作业表' })).toBeInTheDocument();
  expect(screen.getByText('15 / 20 / 0')).toBeInTheDocument();
  expect(screen.getByText(/原宽沿柜长/)).toBeInTheDocument();
  expect(screen.getByText('30 kg')).toBeInTheDocument();
  expect(screen.getByText(/易碎：轻放/)).toBeInTheDocument();
  expect(screen.getByText(/重件：确认搬运设备/)).toBeInTheDocument();
  expect(screen.getByText(/25 kg/)).toHaveTextContent('不是安全或法规标准');
  expect(screen.queryByLabelText('纸面复核签名')).not.toBeInTheDocument();
});

test('screen and print use identical rows and print includes blank review fields', () => {
  render(<><LoadingWorksheet solution={solution} cargoItems={[cargo]} clearanceMm={0} /><LoadingWorksheet solution={solution} cargoItems={[cargo]} clearanceMm={0} print /></>);
  const tables = screen.getAllByRole('table', { name: '重心稳妥逐件作业表' });
  expect(within(tables[0]).getAllByRole('row')[2].textContent).toBe(within(tables[1]).getAllByRole('row')[2].textContent);
  const signature = screen.getByLabelText('纸面复核签名');
  expect(signature).toHaveTextContent('装载人');
  expect(signature).toHaveTextContent('复核人');
  expect(signature).toHaveTextContent('日期');
  expect(signature).toHaveTextContent('固定措施');
});

test('replaces old coordinates after an edited solution is applied', () => {
  const { rerender } = render(<LoadingWorksheet solution={solution} cargoItems={[cargo]} clearanceMm={0} />);
  rerender(<LoadingWorksheet solution={{ ...solution, placements: [{ ...placement, x_mm: 1200, step: 2 }] }} cargoItems={[cargo]} clearanceMm={0} />);
  expect(screen.queryByText('15 / 20 / 0')).not.toBeInTheDocument();
  expect(screen.getByText('120 / 20 / 0')).toBeInTheDocument();
  expect(screen.getByText('第 2 步')).toBeInTheDocument();
});

test('does not create a usable checklist for an empty layout', () => {
  render(<LoadingWorksheet solution={{ ...solution, placements: [] }} cargoItems={[cargo]} clearanceMm={0} print />);
  expect(screen.getByText(/没有已装货物/)).toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('纸面复核签名')).not.toBeInTheDocument();
});
