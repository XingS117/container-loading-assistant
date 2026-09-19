import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SolutionAssessmentPanel } from './SolutionAssessment';
import { explainFloorRisk } from './SolutionWorkspace';
import type { PackingSolution } from '../types';

const solution: PackingSolution = {profile:'easy',name:'easy',placements:[],loaded_counts:{},unloaded_counts:{},zones:[],pros:[],cons:[],warnings:[],identical_to:null,
  metrics:{loaded_pieces:9,loading_steps:3,length_imbalance_pct:2,width_imbalance_pct:1,loaded_weight_g:9,volume_utilization_pct:1,weight_utilization_pct:1,weight_imbalance_pct:2,cargo_zones:1,center_of_gravity:{x_mm:0,y_mm:0,z_mm:0}},
  assessment:{status:'budget_fallback',goal:'减少操作',hard_constraints:['完整支撑'],unmet_soft_goals:['预算用尽'],
    limits:'不保证全局最优',tradeoffs:[],deltas:{},diagnostics:{complete:true,floor_void_m2:1,upper_max_void_m2:0.5,
      large_void_count:1,large_void_area_m2:1,min_support_pct:100,upper_continuity_pct:50,upper_fragment_count:2,
      sku_switches:3,estimated_handling_distance_m:20,regions:[{id:'gap',x_mm:1000,y_mm:0,z_mm:500,length_mm:500,width_mm:1000,area_m2:0.5,placement_ids:['a-0']}]}}};

test('shows real tradeoffs and exposes region location to the viewer', async () => {
  const focus=vi.fn();
  render(<SolutionAssessmentPanel solution={solution} baseline={{...solution,metrics:{...solution.metrics,loaded_pieces:10,loading_steps:4}}} onFocus={focus} />);
  await userEvent.click(screen.getByText('目标达成与布局依据'));
  expect(screen.getByText(/预算内安全备选/)).toBeInTheDocument();
  expect(screen.getByText(/少装 1 件/)).toBeInTheDocument();
  expect(screen.getByText(/100%/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:/定位空白/}));
  expect(focus).toHaveBeenCalledWith(solution.assessment!.diagnostics.regions[0]);
});

test('old history cannot display invented quality measurements', () => {
  render(<SolutionAssessmentPanel solution={{...solution,assessment:null}} baseline={solution} />);
  expect(screen.getByText(/历史方案没有几何解释数据/)).toBeInTheDocument();
});

test('full direct support is distinguished from lateral gaps and transport stability', () => {
  const assessed={...solution,metrics:{...solution.metrics,floor_largest_gap_mm:300,floor_largest_transverse_gap_mm:0}};
  expect(explainFloorRisk(assessed)).toContain('未造成底面悬空');
  expect(explainFloorRisk(assessed)).toContain('侧向稳定');
});
