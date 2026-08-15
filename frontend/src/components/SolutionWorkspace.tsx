import { AlertTriangle, ArrowLeft, CheckCircle2, Printer, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { LoadVisualizer, StaticLayout } from "./LoadVisualizer";
import type { CargoInput, ContainerSpec, OptimizationGoal, PackResponse } from "../types";

interface Props {
  response: PackResponse;
  container: ContainerSpec;
  presets: ContainerSpec[];
  cargoItems: CargoInput[];
  goal: OptimizationGoal;
  onBack: () => void;
  onRecalculate: (container: ContainerSpec, goal: OptimizationGoal) => Promise<void>;
  recalculating: boolean;
}

/** 优化目标偏好：切换后重新计算，每次只展示当前目标的单个方案 */
const GOAL_OPTIONS: { id: OptimizationGoal; name: string; badge: string; description: string }[] = [
  { id: "high_fill", name: "装载率优先", badge: "装载率", description: "优先装满柜体，最大化装载量" },
  { id: "stable", name: "重心稳妥", badge: "重心", description: "重货集中中间，行驶更稳妥" },
  { id: "easy", name: "易操作", badge: "步骤", description: "分区更少，现场操作更省事" },
];

const GOAL_NAME: Record<OptimizationGoal, string> = {
  high_fill: "装载率优先",
  stable: "重心稳妥",
  easy: "易操作",
};

export function SolutionWorkspace({ response, container, presets, cargoItems, goal, onBack, onRecalculate, recalculating }: Props) {
  const [selectedCargoId, setSelectedCargoId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [recalculateContainerId, setRecalculateContainerId] = useState(container.id);
  const [recalculateError, setRecalculateError] = useState<string | null>(null);
  const [identicalNotice, setIdenticalNotice] = useState<string | null>(null);
  const previousLayoutRef = useRef<{ goal: OptimizationGoal; fingerprint: string } | null>(null);
  const selected = response.solutions[0];
  const cargoById = Object.fromEntries(cargoItems.map((item) => [item.id, item]));
  useEffect(() => {
    setSnapshot(null);
  }, [response.request_id]);
  useEffect(() => {
    // 切换优化目标后对比平移归一指纹：几何相同（或仅整体平移）时披露，
    // 避免用户看到三个目标产出同一张图却没有任何说明。
    // StrictMode 双跑幂等：第二次运行时 prev.goal === goal 走 else 分支。
    const fingerprint = selected.layout_fingerprint ?? "";
    const previous = previousLayoutRef.current;
    if (
      previous !== null
      && previous.goal !== goal
      && fingerprint !== ""
      && previous.fingerprint === fingerprint
    ) {
      setIdenticalNotice(
        `「${GOAL_NAME[goal]}」与「${GOAL_NAME[previous.goal]}」的装载布局几何相同（仅整体平移），当前货物组合下两种目标收敛到同一排布。`
      );
    } else {
      setIdenticalNotice(null);
    }
    previousLayoutRef.current = { goal, fingerprint };
  }, [response.request_id]);
  const handleSnapshot = useCallback((dataUrl: string) => {
    setSnapshot(dataUrl);
  }, []);
  const recalculateContainers = container.id === "custom" ? [container, ...presets] : presets;
  const selectedRecalculateContainer = recalculateContainers.find((item) => item.id === recalculateContainerId) ?? container;

  useEffect(() => {
    setRecalculateContainerId(container.id);
  }, [container.id]);

  const handleRecalculate = async () => {
    setRecalculateError(null);
    try {
      await onRecalculate(selectedRecalculateContainer, goal);
    } catch (reason) {
      setRecalculateError(reason instanceof Error ? reason.message : "重算失败，请稍后重试");
    }
  };

  const handleGoalSwitch = async (nextGoal: OptimizationGoal) => {
    if (nextGoal === goal || recalculating) return;
    setRecalculateError(null);
    try {
      await onRecalculate(selectedRecalculateContainer, nextGoal);
    } catch (reason) {
      setRecalculateError(reason instanceof Error ? reason.message : "重算失败，请稍后重试");
    }
  };

  return (
    <main className="results-page">
      <header className="result-toolbar no-print">
        <button type="button" className="text-button" onClick={onBack}><ArrowLeft size={17} /> 修改货物</button>
        <div><span className="eyebrow">计算结果 · {response.request_id}</span><h1>装柜方案</h1></div>
        <div className="result-actions">
          <label className="recalculate-control"><span className="visually-hidden">重算柜型</span><select aria-label="重算柜型" value={recalculateContainerId} onChange={(event) => setRecalculateContainerId(event.target.value)} disabled={recalculating}>{recalculateContainers.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button type="button" className="primary-outline-button recalculate-button" title="确认重算" onClick={handleRecalculate} disabled={recalculating}><RefreshCw className={recalculating ? "spin" : undefined} size={17} /><span>{recalculating ? "正在重算" : "确认重算"}</span></button></label>
          <button type="button" className="primary-outline-button" onClick={() => window.print()}><Printer size={17} /> 打印 / PDF</button>
        </div>
      </header>
      {recalculateError && <p className="recalculate-error" role="alert">{recalculateError}</p>}

      <section className="solution-tabs" aria-label="优化目标">
        {GOAL_OPTIONS.map((option) => (
          <button
            key={option.id}
            type="button"
            className={`solution-tab ${goal === option.id ? "is-active" : ""}`}
            aria-pressed={goal === option.id}
            disabled={recalculating}
            onClick={() => handleGoalSwitch(option.id)}
          >
            <span className="solution-tab-name">{option.name}</span>
            <strong>{option.badge}</strong>
            <span>{option.description}{goal === option.id ? " · 当前方案" : " · 点击切换"}</span>
          </button>
        ))}
      </section>

      {goal !== "stable" && selected.metrics.length_imbalance_pct > 10 && (
        <p className="balance-warning" role="alert">
          前后重量偏差较大（{selected.metrics.length_imbalance_pct}%），建议切换到「重心稳妥」目标
          <button type="button" className="text-button" onClick={() => handleGoalSwitch("stable")} disabled={recalculating}>立即切换</button>
        </p>
      )}

      {identicalNotice && (
        <p className="identical-layout-notice no-print" data-testid="identical-layout-notice">
          {identicalNotice}
        </p>
      )}

      <section className="workspace-grid">
        <LoadVisualizer container={container} solution={selected} cargoItems={cargoItems} selectedCargoId={selectedCargoId} onSelectCargo={setSelectedCargoId} onSnapshot={handleSnapshot} />
        <aside className="result-inspector">
          <div className="metric-strip">
            <div><span>体积利用率</span><strong>{selected.metrics.volume_utilization_pct}%</strong></div>
            <div><span>重量利用率</span><strong>{selected.metrics.weight_utilization_pct}%</strong></div>
            <div><span>前后偏差</span><strong>{selected.metrics.length_imbalance_pct}%</strong></div>
            <div><span>左右偏差</span><strong>{selected.metrics.width_imbalance_pct}%</strong></div>
          </div>
          <div className="pros-cons-grid">
            <div className="pros"><h2><CheckCircle2 size={17} /> 优点</h2>{selected.pros.map((item) => <p key={item}>{item}</p>)}</div>
            <div className="cons"><h2><AlertTriangle size={17} /> 注意</h2>{selected.cons.map((item) => <p key={item}>{item}</p>)}</div>
          </div>
          <div className="load-summary">
            <h2>装入明细</h2>
            {cargoItems.map((cargo) => (
              <div className="summary-row" key={cargo.id}>
                <span><i aria-hidden="true" />{cargo.sku}<small>{cargo.name}</small></span>
                <strong>{selected.loaded_counts[cargo.id] ?? 0} / {cargo.quantity}</strong>
                {(selected.unloaded_counts[cargo.id] ?? 0) > 0 && <em>余 {selected.unloaded_counts[cargo.id]} 件</em>}
              </div>
            ))}
          </div>
          {selected.warnings.map((warning) => <p className="result-warning" key={warning}><AlertTriangle size={16} />{warning}</p>)}
        </aside>
      </section>

      <section className="print-only print-report">
        <h1>装柜方案助手</h1>
        <p className="print-meta">{container.name} · {GOAL_NAME[goal]} · 计算编号 {response.request_id} · {new Date().toLocaleString("zh-CN")}</p>
        <h2>方案摘要</h2>
        <table className="print-table">
          <thead>
            <tr><th>优化目标</th><th>装入件数</th><th>体积利用率</th><th>重量利用率</th><th>重心偏差</th><th>装载步骤</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>{GOAL_NAME[selected.profile]}</td>
              <td>{selected.metrics.loaded_pieces} 件</td>
              <td>{selected.metrics.volume_utilization_pct}%</td>
              <td>{selected.metrics.weight_utilization_pct}%</td>
              <td>{selected.metrics.weight_imbalance_pct}%</td>
              <td>{selected.metrics.loading_steps} 步</td>
            </tr>
          </tbody>
        </table>
        <h2>货物清单</h2>
        <table className="print-table">
          <thead>
            <tr><th>SKU</th><th>名称</th><th>类型</th><th>尺寸（长×宽×高 cm）</th><th>单重 kg</th><th>数量</th></tr>
          </thead>
          <tbody>
            {cargoItems.map((cargo) => (
              <tr key={cargo.id}>
                <td>{cargo.sku}</td>
                <td>{cargo.name}</td>
                <td>{cargo.kind === "pallet" ? "整托" : "散箱"}</td>
                <td>{cargo.length_cm} × {cargo.width_cm} × {cargo.height_cm}</td>
                <td>{cargo.weight_kg}</td>
                <td>{cargo.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <section className="print-solution-page">
          <h2>{GOAL_NAME[selected.profile]}方案</h2>
          <div className="print-pros-cons">
            <div><h3>优点</h3>{selected.pros.map((item) => <p key={item}>{item}</p>)}</div>
            <div><h3>注意</h3>{selected.cons.map((item) => <p key={item}>{item}</p>)}</div>
          </div>
          {snapshot && <img className="print-snapshot" src={snapshot} alt={`${GOAL_NAME[selected.profile]}三维装柜布局`} />}
          <h3>装柜图（俯视 · 侧视）</h3>
          <div className="print-layouts">
            <StaticLayout mode="top" container={container} placements={selected.placements} zones={selected.zones} cargoItems={cargoItems} testId="print-top" compact />
            <StaticLayout mode="side" container={container} placements={selected.placements} zones={selected.zones} cargoItems={cargoItems} testId="print-side" compact />
          </div>
          <h3>装入明细</h3>
          <table className="print-table">
            <thead>
              <tr><th>SKU</th><th>名称</th><th>装入</th><th>未装</th><th>订货量</th></tr>
            </thead>
            <tbody>
              {cargoItems.map((cargo) => (
                <tr key={cargo.id}>
                  <td>{cargo.sku}</td>
                  <td>{cargo.name}</td>
                  <td>{selected.loaded_counts[cargo.id] ?? 0} 件</td>
                  <td>{selected.unloaded_counts[cargo.id] ?? 0} 件</td>
                  <td>{cargo.quantity} 件</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected.zones.length > 0 && selected.zones.length <= 30 && (
            <>
              <h3>区域说明</h3>
              <p className="print-zones">
                {selected.zones.map((zone) => (
                  <span key={`${zone.step}-${zone.cargo_id}-${zone.x_mm}-${zone.y_mm}`}>
                    区域 {zone.step}：{cargoById[zone.cargo_id]?.sku ?? zone.cargo_id} ×{zone.piece_count} 件
                    （柜长 {(zone.x_mm / 1000).toFixed(1)}–{((zone.x_mm + zone.length_mm) / 1000).toFixed(1)} m）；
                  </span>
                ))}
              </p>
            </>
          )}
        </section>

        {(() => {
          const layers = [...new Set(selected.placements.map((item) => item.z_mm))].sort((a, b) => a - b);
          if (!layers.length) return null;
          return (
            <section className="print-solution-page">
              <h2>{GOAL_NAME[selected.profile]} · 分层布局（共 {layers.length} 层）</h2>
              <div className="print-layer-grid">
                {layers.map((layer) => (
                  <div className="print-layer" key={layer}>
                    <h4>高 {(layer / 10).toFixed(0)} cm</h4>
                    <StaticLayout mode="layers" container={container} placements={selected.placements.filter((item) => item.z_mm === layer)} zones={selected.zones} cargoItems={cargoItems} testId={`print-layer-${layer}`} compact />
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
