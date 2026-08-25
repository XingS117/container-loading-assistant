import { AlertTriangle, ArrowLeft, CheckCircle2, CircleX, Info, Printer, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { LoadVisualizer, StaticLayout } from "./LoadVisualizer";
import type { CargoInput, ContainerSpec, PackResponse, SolutionProfile } from "../types";

interface Props {
  response: PackResponse;
  container: ContainerSpec;
  presets: ContainerSpec[];
  cargoItems: CargoInput[];
  onBack: () => void;
  onRecalculate: (container: ContainerSpec) => Promise<void>;
  recalculating: boolean;
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

export function recommendProfile(response: PackResponse): SolutionProfile {
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

export function SolutionWorkspace({ response, container, presets, cargoItems, onBack, onRecalculate, recalculating }: Props) {
  const [selectedProfile, setSelectedProfile] = useState<SolutionProfile>(() => recommendProfile(response));
  const [selectedCargoId, setSelectedCargoId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Partial<Record<SolutionProfile, string>>>({});
  const [recalculateContainerId, setRecalculateContainerId] = useState(container.id);
  const [recalculateError, setRecalculateError] = useState<string | null>(null);
  const selected = response.solutions.find((solution) => solution.profile === selectedProfile) ?? response.solutions[0];
  const cargoById = Object.fromEntries(cargoItems.map((item) => [item.id, item]));
  const recommended = recommendProfile(response);
  const aiStrategy = response.ai_strategy;
  const warningGroups = useMemo(() => (
    (["critical", "caution", "info"] as const).map((severity) => ({
      severity,
      warnings: selected.warnings.filter((warning) => classifySolutionWarning(warning) === severity),
    })).filter((group) => group.warnings.length > 0)
  ), [selected.warnings]);
  useEffect(() => {
    setSelectedProfile(recommendProfile(response));
  }, [response.request_id]);
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
      await onRecalculate(selectedRecalculateContainer);
    } catch (reason) {
      setRecalculateError(reason instanceof Error ? reason.message : "重算失败，请稍后重试");
    }
  };

  return (
    <main className="results-page">
      <header className="result-toolbar no-print">
        <button type="button" className="text-button" onClick={onBack}><ArrowLeft size={17} /> 修改货物</button>
        <div><span className="eyebrow">计算结果 · {response.request_id}</span><h1>方案比较</h1></div>
        <div className="result-actions">
          <label className="recalculate-control"><span className="visually-hidden">重算柜型</span><select aria-label="重算柜型" value={recalculateContainerId} onChange={(event) => setRecalculateContainerId(event.target.value)} disabled={recalculating}>{recalculateContainers.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button type="button" className="primary-outline-button recalculate-button" title="确认重算" onClick={handleRecalculate} disabled={recalculating}><RefreshCw className={recalculating ? "spin" : undefined} size={17} /><span>{recalculating ? "正在重算" : "确认重算"}</span></button></label>
          <button type="button" className="primary-outline-button" onClick={() => window.print()}><Printer size={17} /> 打印 / PDF</button>
        </div>
      </header>
      {recalculateError && <p className="recalculate-error" role="alert">{recalculateError}</p>}
      {aiStrategy && <section className={`ai-strategy-status ai-strategy-status--${aiStrategy.status} no-print`} aria-label="AI 策略状态" role="status" aria-live="polite">
        <Sparkles size={18} aria-hidden="true" />
        <div>
          <h2>AI 策略</h2>
          <p>{aiStrategy.message}</p>
          {aiStrategy.applied && <small>
            {aiStrategy.row_groups.length > 0
              ? `已采纳 ${aiStrategy.row_groups.length} 个行组建议`
              : "已采纳 AI 引导候选"}
          </small>}
          {aiStrategy.provider && aiStrategy.model && <small>{aiStrategy.provider} / {aiStrategy.model}</small>}
        </div>
      </section>}

      <section className="solution-tabs" aria-label="装柜方案">
        {response.solutions.map((solution) => {
          const primaryValue = solution.profile === "high_fill"
            ? `${solution.metrics.volume_utilization_pct}%`
            : solution.profile === "stable"
              ? `${solution.metrics.weight_imbalance_pct}%`
              : `${solution.metrics.loading_steps} 步`;
          return (
            <button key={solution.profile} type="button" className={`solution-tab ${selected.profile === solution.profile ? "is-active" : ""}`} onClick={() => setSelectedProfile(solution.profile)}>
              <span className="solution-tab-name">{profileDisplayName[solution.profile]}</span>
              <strong>{primaryValue}</strong>
              <span>{profileShortName[solution.profile]} · {solution.metrics.loaded_pieces} 件</span>
              {recommended === solution.profile && <em className="recommend-badge">推荐</em>}
            </button>
          );
        })}
      </section>

      {selected.profile !== "stable" && selected.metrics.length_imbalance_pct > 10 && (
        <p className="balance-warning" role="alert">
          前后重量偏差较大（{selected.metrics.length_imbalance_pct}%），建议查看「重心稳妥」方案
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
