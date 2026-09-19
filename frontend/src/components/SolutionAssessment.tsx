import type { LayoutRegion, PackingSolution } from '../types';

export function SolutionAssessmentPanel({solution,baseline,onFocus,print=false}:{solution:PackingSolution;baseline:PackingSolution;onFocus?:(region:LayoutRegion)=>void;print?:boolean}) {
  const a=solution.assessment;
  if (!a) return <p className="history-note">历史方案没有几何解释数据；重新计算后可查看，不能据此判断没有风险。</p>;
  const d=a.diagnostics;
  const dropped=baseline.metrics.loaded_pieces-solution.metrics.loaded_pieces;
  const stepDelta=solution.metrics.loading_steps-baseline.metrics.loading_steps;
  const baseDiagnostics=baseline.assessment?.diagnostics;
  const value=(n:number|null,unit:string)=>n===null?'不适用 / 未评估':`${n}${unit}`;
  const body=<>
    <p><strong>{a.goal}</strong> · {{heuristic:'已校验的启发式方案',budget_fallback:'预算内安全备选',manual_review:'人工调整已复核'}[a.status]}</p>
    {solution.profile!=='high_fill' && <p>相对当前高装载率方案：{dropped>0?`少装 ${dropped} 件`:dropped<0?`多装 ${-dropped} 件`:'件数相同'}；步骤变化 {stepDelta>0?'+':''}{stepDelta} 步；前后 / 左右偏差变化 {(solution.metrics.length_imbalance_pct-baseline.metrics.length_imbalance_pct).toFixed(2)} / {(solution.metrics.width_imbalance_pct-baseline.metrics.width_imbalance_pct).toFixed(2)} 个百分点。</p>}
    {solution.profile!=='high_fill' && baseDiagnostics && <p>估算搬运距离变化 {(d.estimated_handling_distance_m-baseDiagnostics.estimated_handling_distance_m).toFixed(2)} m；上层空白变化 {d.upper_max_void_m2!==null && baseDiagnostics.upper_max_void_m2!==null ? `${(d.upper_max_void_m2-baseDiagnostics.upper_max_void_m2).toFixed(3)} m²` : '不适用 / 未评估'}。负数代表减少。</p>}
    <dl className="assessment-metrics">
      <div><dt>底层空白</dt><dd>{value(d.floor_void_m2,' m²')}</dd></div>
      <div><dt>上层最大单层空白</dt><dd>{value(d.upper_max_void_m2,' m²')}</dd></div>
      <div><dt>最小直接支撑率</dt><dd>{value(d.min_support_pct,'%')}</dd></div>
      <div><dt>上层最低连续率</dt><dd>{value(d.upper_continuity_pct,'%')}</dd></div>
      <div><dt>上层最大分区数</dt><dd>{d.complete?d.upper_fragment_count:'未完整评估'}</dd></div>
      <div><dt>SKU 切换</dt><dd>{d.sku_switches} 次</dd></div>
      <div><dt>估算搬运距离</dt><dd>{d.estimated_handling_distance_m} m</dd></div>
      <div><dt>大空白区域</dt><dd>{d.large_void_count} 处 / {d.large_void_area_m2} m²{!d.complete?'（部分分析）':''}</dd></div>
    </dl>
    <p>已校验硬约束：{a.hard_constraints.join('；')}。</p>
    <strong>未满足目标与待复核事项</strong>
    {a.unmet_soft_goals.length?<ul>{a.unmet_soft_goals.map((text,i)=><li key={i}>{text}</li>)}</ul>:<p>当前分析未发现额外软目标提示，仍需现场复核。</p>}
    {a.status==='manual_review' && <p>人工修改后的目标取舍以上述当前指标比较为准，不沿用原始算法推荐。</p>}
    {d.regions.length>0 && <div className="diagnostic-regions">{d.regions.map((region,i)=><p key={region.id}>
      高度 {region.z_mm/10} cm：空白 {region.area_m2} m²，柜长 {region.x_mm/10}–{(region.x_mm+region.length_mm)/10} cm，柜宽 {region.y_mm/10}–{(region.y_mm+region.width_mm)/10} cm。
      {!print&&onFocus&&<button type="button" onClick={()=>onFocus(region)}>定位空白 {i+1}</button>}
    </p>)}</div>}
    {d.large_void_count>d.regions.length&&<p>仅列出前 {d.regions.length} 个区域，其余请结合分层图复核。</p>}
    <p className="history-note">{a.limits}</p>
  </>;
  return print?<section className="solution-assessment">{body}</section>:<details className="solution-assessment no-print"><summary>目标达成与布局依据</summary>{body}</details>;
}
