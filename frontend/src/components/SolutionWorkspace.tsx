import { AlertTriangle, ArrowLeft, CheckCircle2, CircleX, Info, Printer, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { LoadVisualizer, StaticLayout } from "./LoadVisualizer";
import { LayoutWorkbench } from "./LayoutWorkbench";
import type { CargoInput, ContainerSpec, PackResponse, PackingSolution, SolutionProfile } from "../types";
import { trackAnalyticsEvent } from "../lib/analytics";

interface Props {
  response: PackResponse;
  container: ContainerSpec;
  presets: ContainerSpec[];
  cargoItems: CargoInput[];
  onBack: () => void;
  onRecalculate: (container: ContainerSpec, lockedPlacements?: import("../types").Placement[]) => Promise<void>;
  recalculating: boolean;
  itemGapCm?: number;
}

const profileDisplayName: Record<SolutionProfile, string> = {
  high_fill: "装载率优先",
  stable: "重心稳妥",
  easy: "易操作",
};

const profileShortName: Record<SolutionProfile, string> = {
  high_fill: "装载率",
  stable: "重心稳妥",
  easy: "装载步骤",
};

const coordinateProfileNames: Record<SolutionProfile, string> = {
  high_fill: "装载率优先",
  stable: "重心稳妥",
  easy: "易操作",
};

type WarningSeverity = "critical" | "caution" | "info";

const warningMeta: Record<WarningSeverity, { title: string; Icon: typeof AlertTriangle }> = {
  critical: { title: "必须处理", Icon: CircleX },
  caution: { title: "需要现场复核", Icon: AlertTriangle },
  info: { title: "方案信息", Icon: Info },
};

export function classifySolutionWarning(warning: string): WarningSeverity {
  if (/订单总重.*超过柜体最大载重|必装货物.*未全部装入/.test(warning)) return "critical";
  if (/柜门预留操作空间|当前方案仍剩载重/.test(warning)) return "info";
  return "caution";
}

export function explainFloorRisk(solution: PackingSolution): string {
  const largestGap = solution.metrics.floor_largest_gap_mm ?? 0;
  const transverseGap = solution.metrics.floor_largest_transverse_gap_mm ?? 0;
  if (largestGap >= 150 || transverseGap >= 150) {
    return `底层最大空隙 ${Math.max(largestGap, transverseGap)} mm，可能降低上层支撑连续性，需要现场复核并考虑填充或固定`;
  }
  if (largestGap >= 50 || transverseGap >= 50) {
    return `底层存在 ${Math.max(largestGap, transverseGap)} mm 局部空隙，建议确认上层货物底面是否被充分支撑`;
  }
  return `底层最大空隙 ${Math.max(largestGap, transverseGap)} mm，一般可接受，当前未发现明显支撑风险`;
}

export function loadingStepLabels(solution: PackingSolution, cargoItems: CargoInput[]): string[] {
  const names = Object.fromEntries(cargoItems.map((item) => [item.id, item.sku]));
  return [...solution.zones]
    .sort((left, right) => left.step - right.step)
    .map((zone) => `第 ${zone.step} 步：${names[zone.cargo_id] ?? zone.cargo_id} × ${zone.piece_count} 件`);
}

export function recommendProfile(response: PackResponse): SolutionProfile {
  if (response.recommended_profile && response.solutions.some((solution) => solution.profile === response.recommended_profile)) {
    return response.recommended_profile;
  }
  const highFill = response.solutions.find((solution) => solution.profile === "high_fill");
  const stable = response.solutions.find((solution) => solution.profile === "stable");
  if (
    highFill &&
    stable &&
    highFill.metrics.length_imbalance_pct > 10 &&
    stable.metrics.length_imbalance_pct <= highFill.metrics.length_imbalance_pct - 5
  ) {
    return "stable";
  }
  return "high_fill";
}

export function SolutionWorkspace({ response: originalResponse, container, presets, cargoItems, onBack, onRecalculate, recalculating, itemGapCm = 0 }: Props) {
  const [selectedProfile, setSelectedProfile] = useState<SolutionProfile>(() => recommendProfile(originalResponse));
  const [selectedCargoId, setSelectedCargoId] = useState<string | null>(null);
  const [lockedCargoIds, setLockedCargoIds] = useState<Set<string>>(new Set());
  const [editMessage, setEditMessage] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<"accepted" | "needs_adjustment" | null>(null);
  const [adjustmentOpen, setAdjustmentOpen] = useState(false);
  const [adjustmentTopics, setAdjustmentTopics] = useState<string[]>([]);
  const [adjustmentNote, setAdjustmentNote] = useState("");
  const [adjustmentSubmitted, setAdjustmentSubmitted] = useState(false);
  const [snapshots, setSnapshots] = useState<Partial<Record<SolutionProfile, string>>>({});
  const [recalculateContainerId, setRecalculateContainerId] = useState(container.id);
  const [recalculateError, setRecalculateError] = useState<string | null>(null);
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [solutionOverrides, setSolutionOverrides] = useState<Partial<Record<SolutionProfile, PackingSolution>>>({});
  const response = { ...originalResponse, solutions: originalResponse.solutions.map(s => solutionOverrides[s.profile] ?? s) };
  const baseSelected = response.solutions.find((solution) => solution.profile === selectedProfile) ?? response.solutions[0];
  const selected = solutionOverrides[baseSelected.profile] ?? baseSelected;
  const resetEdits = () => {
    setSolutionOverrides(current => { const next = { ...current }; delete next[selectedProfile]; return next; });
    setSnapshots(current => ({ ...current, [selectedProfile]: undefined }));
    setEditMessage("已恢复计算生成的原始布局");
  };
  const cargoById = Object.fromEntries(cargoItems.map((item) => [item.id, item]));
  const recommended = recommendProfile(response);
  const aiStrategy = response.ai_strategy;
  const hasProfileHints = Boolean(aiStrategy?.profiles && Object.keys(aiStrategy.profiles).length > 0);
  const warningGroups = useMemo(() => (
    (["critical", "caution", "info"] as const).map((severity) => ({
      severity,
      warnings: selected.warnings.filter((warning) => classifySolutionWarning(warning) === severity),
    })).filter((group) => group.warnings.length > 0)
  ), [selected.warnings]);
  useEffect(() => {
    setSelectedProfile(recommendProfile(originalResponse));
    setSolutionOverrides({}); setSnapshots({}); setLockedCargoIds(new Set());
    setWorkbenchOpen(false); setEditMessage(null); setFeedback(null);
  }, [originalResponse]);
  const handleSnapshot = useCallback((dataUrl: string) => {
    setSnapshots((current) => ({ ...current, [selectedProfile]: dataUrl }));
  }, [selectedProfile]);
  const recalculateContainers = container.id === "custom" ? [container, ...presets] : presets;
  const selectedRecalculateContainer = recalculateContainers.find((item) => item.id === recalculateContainerId) ?? container;

  useEffect(() => {
    setRecalculateContainerId(container.id);
  }, [container.id]);

  const handleRecalculate = async () => {
    setRecalculateError(null);
    try {
      const lockedPlacements = selected.placements.filter((placement) => lockedCargoIds.has(placement.cargo_id));
      await onRecalculate(selectedRecalculateContainer, lockedPlacements);
    } catch (reason) {
      setRecalculateError(reason instanceof Error ? reason.message : "重算失败，请稍后重试");
    }
  };

  return (
    <main className="results-page">
      {workbenchOpen && <LayoutWorkbench solution={selected} container={container} cargoItems={cargoItems} itemGapCm={itemGapCm} initialLockedCargoIds={lockedCargoIds} onClose={() => setWorkbenchOpen(false)} onApply={(nextSolution, locks) => { setSolutionOverrides((current) => ({ ...current, [nextSolution.profile]: nextSolution })); setLockedCargoIds(locks); setSnapshots(current => ({ ...current, [nextSolution.profile]: undefined })); setWorkbenchOpen(false); setEditMessage("布局已应用，指标与安全复核结果已更新"); trackAnalyticsEvent("pack_layout_workbench_applied", { profile: nextSolution.profile }); }} />}
      <header className="result-toolbar no-print">
        <button type="button" className="text-button" onClick={onBack}><ArrowLeft size={17} /> 修改货物</button>
        <div><span className="eyebrow">计算结果 · {response.request_id}</span><h1>方案比较</h1></div>
        <div className="result-actions">
          <button type="button" className="primary-outline-button" onClick={() => setWorkbenchOpen(true)}>编辑布局</button>
          <label className="recalculate-control"><span className="visually-hidden">重算柜型</span><select aria-label="重算柜型" value={recalculateContainerId} onChange={(event) => setRecalculateContainerId(event.target.value)} disabled={recalculating}>{recalculateContainers.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button type="button" className="primary-outline-button recalculate-button" title="确认重算" onClick={handleRecalculate} disabled={recalculating}><RefreshCw className={recalculating ? "spin" : undefined} size={17} /><span>{recalculating ? "正在重算" : "确认重算"}</span></button></label>
          <button type="button" className="primary-outline-button" onClick={() => { trackAnalyticsEvent("pack_export_print", { profile: selectedProfile }); window.print(); }}><Printer size={17} /> 打印 / PDF</button>
        </div>
      </header>
      {recalculateError && <p className="recalculate-error" role="alert">{recalculateError}</p>}
      {solutionOverrides[selectedProfile] && <div className="edit-summary no-print" role="status"><strong>当前方案已人工调整</strong><span>布局与指标已通过后端规则复核；打印使用本次应用的布局。</span><button type="button" onClick={resetEdits}>恢复原始布局</button></div>}

      <section className="solution-tabs" aria-label="装柜方案">
        {response.solutions.map((baseSolution) => { const solution = solutionOverrides[baseSolution.profile] ?? baseSolution;
          const primaryValue = solution.profile === "high_fill"
            ? `${solution.metrics.volume_utilization_pct}%`
            : solution.profile === "stable"
              ? `${solution.metrics.weight_imbalance_pct}%`
              : `${solution.metrics.loading_steps} 步`;
          return (
            <button key={solution.profile} type="button" className={`solution-tab ${selected.profile === solution.profile ? "is-active" : ""}`} onClick={() => { trackAnalyticsEvent("pack_solution_selected", { profile: solution.profile }); setSelectedProfile(solution.profile); }}>
              <span className="solution-tab-name">{profileDisplayName[solution.profile]}</span>
              <strong>{primaryValue}</strong>
              <span>{profileShortName[solution.profile]} · {solution.metrics.loaded_pieces} 件</span>
              {recommended === solution.profile && <em className="recommend-badge">推荐</em>}
            </button>
          );
        })}
      </section>

      <details className="solution-comparison no-print" aria-label="方案指标对比">
        <summary>方案指标对比 <span>展开查看三种方案的详细指标</span></summary>
        <div className="comparison-table-wrap">
          <table className="comparison-table">
            <thead><tr><th>指标</th>{response.solutions.map((baseSolution) => { const solution = solutionOverrides[baseSolution.profile] ?? baseSolution; return <th key={solution.profile}>{profileDisplayName[solution.profile]}{recommended === solution.profile ? " · 推荐" : ""}</th>; })}</tr></thead>
            <tbody>
              {(["loaded_pieces", "volume_utilization_pct", "length_imbalance_pct", "floor_largest_gap_mm", "loading_steps"] as const).map((metric, index) => <tr key={metric}><th>{["装入件数", "体积利用率", "前后偏差", "底层最大空隙", "装载步数"][index]}</th>{response.solutions.map(solution => <td key={solution.profile}>{solution.metrics[metric] ?? 0}{[" 件", "%", "%", " mm", " 步"][index]}</td>)}</tr>)}
            </tbody>
          </table>
        </div>
      </details>

      {selected.profile !== "stable" && selected.metrics.length_imbalance_pct > 10 && (
        <p className="balance-warning" role="alert">
          前后重量偏差较大（{selected.metrics.length_imbalance_pct}%），建议查看「重心稳妥」方案
        </p>
      )}

      <section className="workspace-grid">
        <LoadVisualizer container={container} solution={selected} cargoItems={cargoItems} selectedCargoId={selectedCargoId} onSelectCargo={setSelectedCargoId} onSnapshot={handleSnapshot} />
        <aside className="result-inspector">
          {editMessage && <p className="edit-message" role="status">{editMessage}</p>}
          <div className="metric-strip">
            <div><span>体积利用率</span><strong>{selected.metrics.volume_utilization_pct}%</strong></div>
            <div><span>重量利用率</span><strong>{selected.metrics.weight_utilization_pct}%</strong></div>
            <div><span>前后偏差</span><strong>{selected.metrics.length_imbalance_pct}%</strong></div>
            <div><span>左右偏差</span><strong>{selected.metrics.width_imbalance_pct}%</strong></div>
            {selected.metrics.floor_largest_gap_mm !== undefined && <div><span>底层最大空隙</span><strong>{selected.metrics.floor_largest_gap_mm} mm</strong></div>}
            {selected.metrics.floor_largest_transverse_gap_mm !== undefined && <div><span>底层横向断层</span><strong>{selected.metrics.floor_largest_transverse_gap_mm} mm</strong></div>}
          </div>
          <div className="pros-cons-grid">
            <div className="pros"><h2><CheckCircle2 size={17} /> 优点</h2>{selected.pros.map((item) => <p key={item}>{item}</p>)}</div>
            <div className="cons"><h2><AlertTriangle size={17} /> 注意</h2>{selected.cons.map((item) => <p key={item}>{item}</p>)}</div>
          </div>
          <div className="load-summary">
            <h2>空隙与支撑判断</h2>
            <p>{explainFloorRisk(selected)}</p>
          </div>
          <div className="load-summary">
            <h2>装载步骤</h2>
            {loadingStepLabels(selected, cargoItems).length > 0
              ? loadingStepLabels(selected, cargoItems).map((step) => <p key={step}>{step}</p>)
              : <p>当前方案暂无可拆分的区域步骤，请按 3D 图和装入明细现场复核。</p>}
          </div>
          <div className="load-summary">
            <h2>装入明细</h2>
            {cargoItems.map((cargo) => (
              <div className="summary-row" key={cargo.id}>
                <span><i aria-hidden="true" />{cargo.sku}</span>
                <strong>{selected.loaded_counts[cargo.id] ?? 0} / {cargo.quantity}</strong>
                {(selected.unloaded_counts[cargo.id] ?? 0) > 0 && <em>余 {selected.unloaded_counts[cargo.id]} 件</em>}
              </div>
            ))}
          </div>
          {warningGroups.length > 0 && <section className="result-notices" aria-label="方案提示">
            {warningGroups.map(({ severity, warnings }) => {
              const { title, Icon } = warningMeta[severity];
              return <div className={`notice-group notice-group--${severity}`} key={severity}>
                <h2><Icon size={16} />{title}</h2>
                {warnings.map((warning) => <p className="result-warning" key={warning}>{warning}</p>)}
              </div>;
            })}
          </section>}
        </aside>
      </section>

      <div className="solution-feedback no-print" role="group" aria-label="方案反馈">
        <span>这个方案对你有帮助吗？</span>
        <button type="button" className={feedback === "accepted" ? "is-selected" : ""} onClick={() => { setFeedback("accepted"); trackAnalyticsEvent("pack_solution_feedback", { profile: selectedProfile, result: "accepted" }); }}>满意</button>
        <button type="button" className={feedback === "needs_adjustment" ? "is-selected" : ""} onClick={() => { setFeedback("needs_adjustment"); setAdjustmentOpen(true); setAdjustmentSubmitted(false); trackAnalyticsEvent("pack_solution_feedback", { profile: selectedProfile, result: "needs_adjustment" }); }}>需要调整</button>
      </div>
      {adjustmentOpen && <section className="adjustment-panel no-print" aria-label="方案调整说明">
        <div>
          <h2>告诉我们希望怎么调整</h2>
          <p>反馈会帮助定位问题；提交后当前布局不会自动改变。你可以先进入人工调整，或重新计算一套新方案。</p>
        </div>
        <div className="adjustment-topics" role="group" aria-label="调整原因">
          {["中间空隙太大", "上层支撑不连续", "重心需要更稳", "希望保持更多件数", "希望更易装卸"].map((topic) => (
            <label key={topic}><input type="checkbox" checked={adjustmentTopics.includes(topic)} onChange={(event) => setAdjustmentTopics((current) => event.target.checked ? [...current, topic] : current.filter((item) => item !== topic))} />{topic}</label>
          ))}
        </div>
        <textarea aria-label="补充调整要求" placeholder="例如：把深绿色货物向中间集中，数量少的货物放两侧" value={adjustmentNote} onChange={(event) => setAdjustmentNote(event.target.value)} rows={3} />
        <div className="adjustment-actions">
          <button type="button" className="primary-outline-button" onClick={() => { setAdjustmentSubmitted(true); trackAnalyticsEvent("pack_solution_adjustment_submitted", { profile: selectedProfile, topics: adjustmentTopics.join("|") || "none", has_note: Boolean(adjustmentNote.trim()) }); }}>提交调整说明</button>
          <button type="button" className="text-button" onClick={() => setWorkbenchOpen(true)}>进入人工调整</button>
          <button type="button" className="text-button" onClick={handleRecalculate} disabled={recalculating}>重新计算</button>
          <button type="button" className="text-button" onClick={() => setAdjustmentOpen(false)}>收起</button>
        </div>
        {adjustmentSubmitted && <p className="adjustment-confirmation" role="status">调整说明已记录。当前布局未改变；说明仅暂存本页，文字尚未发送给算法。请进入人工调整应用具体位置。</p>}
      </section>}
      {aiStrategy && <section className={`ai-strategy-status ai-strategy-status--${aiStrategy.status} no-print`} aria-label="AI 策略状态" role="status" aria-live="polite">
        <Sparkles size={18} aria-hidden="true" />
        <div>
          <h2>AI 策略</h2>
          <p>{aiStrategy.message}</p>
          {hasProfileHints && <small>三种方案按目标分别优化</small>}
          {aiStrategy.applied && <small>
            {aiStrategy.row_groups.length > 0
              ? `已采纳 ${aiStrategy.row_groups.length} 个行组建议`
              : "已采纳 AI 引导候选"}
          </small>}
          {aiStrategy.coordinate_candidates_applied && aiStrategy.coordinate_candidates_applied.length > 0 && (
            <small>
              已采纳经校验的 AI 坐标候选：{aiStrategy.coordinate_candidates_applied.map((profile) => coordinateProfileNames[profile]).join("、")}
            </small>
          )}
          {aiStrategy.provider && aiStrategy.model && <small>{aiStrategy.provider} / {aiStrategy.model}</small>}
        </div>
      </section>}

      <section className="print-only print-report">
        <h1>装柜方案助手</h1>
        <p className="print-meta">{container.name} · 计算编号 {response.request_id} · {new Date().toLocaleString("zh-CN")}</p>
        <h2>装柜方案一览</h2>
        <table className="print-table">
          <thead>
            <tr><th>方案</th><th>推荐方案</th><th>装入件数</th><th>体积利用率</th><th>重量利用率</th><th>重心偏差</th><th>装载步骤</th></tr>
          </thead>
          <tbody>
            {response.solutions.map((solution) => (
              <tr key={solution.profile}>
                <td>{profileDisplayName[solution.profile]}</td>
                <td>{recommended === solution.profile ? "★" : ""}</td>
                <td>{solution.metrics.loaded_pieces} 件</td>
                <td>{solution.metrics.volume_utilization_pct}%</td>
                <td>{solution.metrics.weight_utilization_pct}%</td>
                <td>{solution.metrics.weight_imbalance_pct}%</td>
                <td>{solution.metrics.loading_steps} 步</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="print-recommend">推荐方案：{profileDisplayName[recommended]}（
          {recommended === "high_fill" ? "装载率优先" : "在保持装载的前提下降低重心偏差"}）
        </p>
        <h2>货物清单</h2>
        <table className="print-table">
          <thead>
            <tr><th>货物代号/名称</th><th>类型</th><th>尺寸（长×宽×高 cm）</th><th>单重 kg</th><th>数量</th></tr>
          </thead>
          <tbody>
            {cargoItems.map((cargo) => (
              <tr key={cargo.id}>
                <td>{cargo.sku}</td>
                <td>{cargo.kind === "pallet" ? "整托" : "散箱"}</td>
                <td>{cargo.length_cm} × {cargo.width_cm} × {cargo.height_cm}</td>
                <td>{cargo.weight_kg}</td>
                <td>{cargo.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {response.solutions.map((solution) => (
          <section className="print-solution-page" key={solution.profile}>
            <h2>{profileDisplayName[solution.profile]}{recommended === solution.profile ? " ★ 推荐" : ""}</h2>
            <div className="print-pros-cons">
              <div><h3>优点</h3>{solution.pros.map((item) => <p key={item}>{item}</p>)}</div>
              <div><h3>注意</h3>{solution.cons.map((item) => <p key={item}>{item}</p>)}</div>
            </div>
            <h3>风险摘要</h3>
            <p>{explainFloorRisk(solution)}</p>
            <h3>装载步骤（从柜门向柜内）</h3>
            <div className="print-steps">
              {loadingStepLabels(solution, cargoItems).length > 0
                ? loadingStepLabels(solution, cargoItems).map((step) => <p key={step}>{step}</p>)
                : <p>暂无区域步骤，请结合装载图现场复核。</p>}
            </div>
            {snapshots[solution.profile] && <img className="print-snapshot" src={snapshots[solution.profile]} alt={`${profileDisplayName[solution.profile]}三维装柜布局`} />}
            <h3>装柜图（俯视 · 侧视）</h3>
            <div className="print-layouts">
              <StaticLayout mode="top" container={container} placements={solution.placements} zones={solution.zones} cargoItems={cargoItems} testId={`print-top-${solution.profile}`} compact />
              <StaticLayout mode="side" container={container} placements={solution.placements} zones={solution.zones} cargoItems={cargoItems} testId={`print-side-${solution.profile}`} compact />
            </div>
            <h3>装入明细</h3>
            <table className="print-table">
              <thead>
                <tr><th>货物代号/名称</th><th>装入</th><th>未装</th><th>订货量</th></tr>
              </thead>
              <tbody>
                {cargoItems.map((cargo) => (
                  <tr key={cargo.id}>
                    <td>{cargo.sku}</td>
                    <td>{solution.loaded_counts[cargo.id] ?? 0} 件</td>
                    <td>{solution.unloaded_counts[cargo.id] ?? 0} 件</td>
                    <td>{cargo.quantity} 件</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {solution.zones.length > 0 && solution.zones.length <= 30 && (
              <>
                <h3>区域说明</h3>
                <p className="print-zones">
                  {solution.zones.map((zone) => (
                    <span key={`${zone.step}-${zone.cargo_id}-${zone.x_mm}-${zone.y_mm}`}>
                      区域 {zone.step}：{cargoById[zone.cargo_id]?.sku ?? zone.cargo_id} ×{zone.piece_count} 件
                      （柜长 {(zone.x_mm / 1000).toFixed(1)}–{((zone.x_mm + zone.length_mm) / 1000).toFixed(1)} m）；
                    </span>
                  ))}
                </p>
              </>
            )}
          </section>
        ))}

        {(() => {
          const rec = response.solutions.find((solution) => solution.profile === recommended) ?? response.solutions[0];
          const recLayers = [...new Set(rec.placements.map((item) => item.z_mm))].sort((a, b) => a - b);
          if (!recLayers.length) return null;
          return (
            <section className="print-solution-page">
              <h2>{profileDisplayName[rec.profile]} · 分层布局（共 {recLayers.length} 层）</h2>
              <div className="print-layer-grid">
                {recLayers.map((layer) => (
                  <div className="print-layer" key={layer}>
                    <h4>高 {(layer / 10).toFixed(0)} cm</h4>
                    <StaticLayout mode="layers" container={container} placements={rec.placements.filter((item) => item.z_mm === layer)} zones={rec.zones} cargoItems={cargoItems} testId={`print-layer-${layer}`} compact />
                  </div>
                ))}
              </div>
            </section>
          );
        })()}
      </section>
    </main>
  );
}
