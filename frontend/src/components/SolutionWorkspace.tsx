import { AlertTriangle, ArrowLeft, CheckCircle2, Printer, RefreshCw } from "lucide-react";
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

const profileShortName: Record<SolutionProfile, string> = {
  high_fill: "装载率",
  stable: "重心偏差",
  easy: "装载步骤",
};

export function SolutionWorkspace({ response, container, presets, cargoItems, onBack, onRecalculate, recalculating }: Props) {
  const [selectedProfile, setSelectedProfile] = useState<SolutionProfile>("high_fill");
  const [selectedCargoId, setSelectedCargoId] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<Partial<Record<SolutionProfile, string>>>({});
  const [recalculateContainerId, setRecalculateContainerId] = useState(container.id);
  const [recalculateError, setRecalculateError] = useState<string | null>(null);
  const selected = response.solutions.find((solution) => solution.profile === selectedProfile) ?? response.solutions[0];
  const cargoById = Object.fromEntries(cargoItems.map((item) => [item.id, item]));
  const selectedLayers = useMemo(
    () => [...new Set(selected.placements.map((item) => item.z_mm))].sort((a, b) => a - b),
    [selected.placements],
  );
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

      <section className="solution-tabs" aria-label="装柜方案">
        {response.solutions.map((solution) => {
          const primaryValue = solution.profile === "high_fill"
            ? `${solution.metrics.volume_utilization_pct}%`
            : solution.profile === "stable"
              ? `${solution.metrics.weight_imbalance_pct}%`
              : `${solution.metrics.loading_steps} 步`;
          return (
            <button key={solution.profile} type="button" className={`solution-tab ${selected.profile === solution.profile ? "is-active" : ""}`} onClick={() => setSelectedProfile(solution.profile)}>
              <span className="solution-tab-name">{solution.name}</span>
              <strong>{primaryValue}</strong>
              <span>{profileShortName[solution.profile]} · {solution.metrics.loaded_pieces} 件</span>
              {solution.identical_to && <em>布局与另一方案相同</em>}
            </button>
          );
        })}
      </section>

      <section className="workspace-grid">
        <LoadVisualizer container={container} solution={selected} cargoItems={cargoItems} selectedCargoId={selectedCargoId} onSelectCargo={setSelectedCargoId} onSnapshot={handleSnapshot} />
        <aside className="result-inspector">
          <div className="metric-strip">
            <div><span>体积利用率</span><strong>{selected.metrics.volume_utilization_pct}%</strong></div>
            <div><span>重量利用率</span><strong>{selected.metrics.weight_utilization_pct}%</strong></div>
            <div><span>重心偏差</span><strong>{selected.metrics.weight_imbalance_pct}%</strong></div>
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
        <p className="print-meta">{container.name} · 计算编号 {response.request_id}</p>
        <h2>三方案比较</h2>
        <div className="print-comparison">
          {response.solutions.map((solution) => (
            <div key={solution.profile}>
              <h3>{solution.name}</h3>
              <strong>{solution.metrics.volume_utilization_pct}%</strong>
              <p>装入 {solution.metrics.loaded_pieces} 件 · 重量 {solution.metrics.weight_utilization_pct}%</p>
              <p>重心偏差 {solution.metrics.weight_imbalance_pct}% · {solution.metrics.loading_steps} 步</p>
            </div>
          ))}
        </div>

        <div className="print-page-break">
          <h2>{selected.name} · 方案说明</h2>
          <div className="print-pros-cons">
            <div><h3>优点</h3>{selected.pros.map((item) => <p key={item}>{item}</p>)}</div>
            <div><h3>注意</h3>{selected.cons.map((item) => <p key={item}>{item}</p>)}</div>
          </div>
          {snapshots[selected.profile] && <img className="print-snapshot" src={snapshots[selected.profile]} alt={`${selected.name}三维装柜布局`} />}
        </div>
        <div className="print-layouts"><StaticLayout mode="top" container={container} placements={selected.placements} cargoItems={cargoItems} testId="print-top-layout" /><StaticLayout mode="side" container={container} placements={selected.placements} cargoItems={cargoItems} testId="print-side-layout" /></div>
        {selectedLayers.map((layer) => (
          <div className="print-layer" key={layer}>
            <h3>分层布局 · {(layer / 10).toFixed(1)} cm</h3>
            <StaticLayout mode="layers" container={container} placements={selected.placements.filter((item) => item.z_mm === layer)} cargoItems={cargoItems} testId={`print-layer-${layer}`} />
          </div>
        ))}
        <h2>装入明细</h2>
        {Object.entries(selected.loaded_counts).map(([id, count]) => <p key={id}>{cargoById[id]?.sku ?? id}：{count} 件，未装 {selected.unloaded_counts[id] ?? 0} 件</p>)}
      </section>
    </main>
  );
}
