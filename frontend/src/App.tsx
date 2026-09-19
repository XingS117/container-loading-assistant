import { Box, Calculator, FileSpreadsheet, LoaderCircle, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CargoTable } from "./components/CargoTable";
import { ContainerPicker } from "./components/ContainerPicker";
import { SolutionWorkspace } from "./components/SolutionWorkspace";
import { ModelSettings } from "./components/ModelSettings";
import { CalculationProgress } from './components/CalculationProgress';
import voyageBanner from "./assets/voyage-banner.jpg";
import { CalculationError, getContainerPresets, packOrder, testAIConnection, type CalculationPhase } from "./lib/api";
import { loadAIConfig, saveAIConfig } from "./lib/aiConfig";
import { createCargo, validateCargo, validateCargoIssues, validateCalculationSettings } from "./lib/cargo";
import { cloneCargoPreset } from "./lib/cargoPresets";
import { downloadCargoTemplate, readCargoExcelReport, type ExcelReport } from "./lib/excel";
import { trackAnalyticsEvent } from "./lib/analytics";
import { loadSavedOrders, saveOrder } from "./lib/orderHistory";
import type { AIModelConfig, CargoInput, CargoPreset, ContainerSpec, PackResponse, SolutionProfile } from "./types";

const STORAGE_KEY = "container-loading-assistant-draft-v1";

interface Draft {
  containerId: string;
  container: ContainerSpec;
  cargoItems: CargoInput[];
  itemGapCm: number;
  clearanceCm: number;
}

function loadDraft(): Partial<Draft> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export default function App() {
  const draft = loadDraft();
  const [presets, setPresets] = useState<ContainerSpec[]>([]);
  const [container, setContainer] = useState<ContainerSpec | null>(null);
  const [cargoItems, setCargoItems] = useState<CargoInput[]>(draft.cargoItems?.length ? draft.cargoItems : [createCargo("SKU-001")]);
  const [itemGapCm, setItemGapCm] = useState(draft.itemGapCm ?? 0);
  const [clearanceCm, setClearanceCm] = useState(draft.clearanceCm ?? 0);
  const [preferredProfile, setPreferredProfile] = useState<SolutionProfile>("high_fill");
  const [aiConfig, setAIConfig] = useState<AIModelConfig>(loadAIConfig);
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [result, setResult] = useState<PackResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const calculationPending = useRef(false);
  const [progress, setProgress] = useState<{phase: CalculationPhase; startedAt: number} | null>(null);
  const [calculationFailed, setCalculationFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedOrders, setSavedOrders] = useState(() => loadSavedOrders());
  const [importReport, setImportReport] = useState<ExcelReport | null>(null);
  const [importing, setImporting] = useState(false);
  const cargoValidationError = validateCargo(cargoItems);
  const cargoValidationIssues = validateCargoIssues(cargoItems);
  const settingsError = validateCalculationSettings(container, itemGapCm, clearanceCm);
  const focusIssue = (row: number, field: string) => {
    const inputs = document.querySelector(`[data-cargo-row="${row}"]`)?.querySelectorAll<HTMLInputElement>('input');
    const input = Array.from(inputs ?? []).find(el => el.getAttribute('aria-label')?.startsWith(field === '货物代号' ? '货物代号或名称 ' : `${field} `));
    input?.scrollIntoView?.({ block: 'center', behavior: 'smooth' }); input?.focus();
  };

  useEffect(() => {
    getContainerPresets()
      .then((items) => {
        setPresets(items);
        const selected = draft.container?.id === "custom"
          ? draft.container
          : items.find((item) => item.id === draft.containerId) ?? items[0];
        setContainer(selected ?? null);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!container) return;
    const nextDraft: Draft = { containerId: container.id, container, cargoItems, itemGapCm, clearanceCm };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(nextDraft));
  }, [container, cargoItems, itemGapCm, clearanceCm]);

  const calculateFor = async (nextContainer: ContainerSpec, lockedPlacements: import("./types").Placement[] = []) => {
    if (calculationPending.current) return;
    const validationError = validateCargo(cargoItems);
    if (validationError) throw new Error(validationError);
    const nextSettingsError = validateCalculationSettings(nextContainer, itemGapCm, clearanceCm);
    if (nextSettingsError) throw new Error(nextSettingsError);
    calculationPending.current = true;
    const startedAt = Date.now();
    const mode = result ? 'recalculate' : 'initial';
    setProgress({phase:'submitting', startedAt});
    setCalculationFailed(false);
    setLoading(true);
    setError(null);
    trackAnalyticsEvent("pack_calculation_started", { mode, cargo_types: cargoItems.length, pieces: cargoItems.reduce((sum, item) => sum + item.quantity, 0), preferred_profile: preferredProfile });
    try {
      const requestContainer = { ...nextContainer, clearance_mm: Math.round(clearanceCm * 10) };
      const nextResult = await packOrder(requestContainer, cargoItems, itemGapCm, aiConfig, preferredProfile, lockedPlacements, phase => setProgress({phase, startedAt}));
      if (!nextResult.solutions.length) throw new CalculationError('服务未返回装柜方案，清单和原方案已保留，请稍后重试。', 'service', 'EMPTY_SOLUTIONS');
      setContainer(requestContainer);
      setResult(nextResult);
      trackAnalyticsEvent("pack_solutions_generated", { mode, elapsed_ms: Date.now() - startedAt, cargo_types: cargoItems.length, pieces: cargoItems.reduce((sum, item) => sum + item.quantity, 0), recommended_profile: nextResult.recommended_profile ?? preferredProfile });
    } catch (reason) {
      setCalculationFailed(true);
      trackAnalyticsEvent('pack_calculation_failed', { mode, elapsed_ms: Date.now() - startedAt, category: reason instanceof CalculationError ? reason.category : 'service', reason: reason instanceof CalculationError ? reason.code : 'UNKNOWN' });
      throw reason;
    } finally {
      calculationPending.current = false;
      setProgress(null);
      setLoading(false);
    }
  };

  const calculate = async () => {
    if (!container) return;
    try {
      await calculateFor(container);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "计算失败，请稍后重试");
    }
  };

  const saveModelConfig = (nextConfig: AIModelConfig) => {
    setAIConfig(nextConfig);
    saveAIConfig(nextConfig);
    setShowModelSettings(false);
  };

  const loadPreset = (preset: CargoPreset) => {
    if (cargoItems.length > 0 && !window.confirm("加载常见规格将替换当前货物清单，是否继续？")) {
      return;
    }
    const hintedContainer = presets.find(
      (item) =>
        item.name === preset.containerHint
        || item.id.toLowerCase() === preset.containerHint.toLowerCase(),
    );
    if (hintedContainer) setContainer(hintedContainer);
    setCargoItems(cloneCargoPreset(preset));
    trackAnalyticsEvent("cargo_preset_loaded", { preset: preset.id });
    setResult(null);
    setError(null);
  };

  const clearDraft = () => {
    localStorage.removeItem(STORAGE_KEY);
    setCargoItems([createCargo("SKU-001")]);
    setContainer(presets[0] ?? null);
    setItemGapCm(0);
    setClearanceCm(0);
    setError(null);
  };

  const saveCurrentOrder = () => {
    if (!container) return;
    const saved = saveOrder({ name: `订单 ${new Date().toLocaleDateString("zh-CN")}`, container, cargoItems, itemGapCm, clearanceCm });
    setSavedOrders((current) => [saved, ...current.filter((item) => item.name !== saved.name)].slice(0, 10));
    setError("订单已保存到本机，可在下方恢复");
  };

  if (result && container) {
    return <><fieldset className="calculation-fields" disabled={loading} aria-busy={loading}><SolutionWorkspace response={result} container={container} presets={presets} cargoItems={cargoItems} itemGapCm={itemGapCm} onBack={() => { trackAnalyticsEvent("pack_edit_input"); setResult(null); setCalculationFailed(false); }} onRecalculate={calculateFor} recalculating={loading} /></fieldset>{progress && <CalculationProgress {...progress} />}</>;
  }

  if (showModelSettings) {
    return <ModelSettings config={aiConfig} onBack={() => setShowModelSettings(false)} onSave={saveModelConfig} onTest={testAIConnection} />;
  }

  return (
    <main className="app-shell">
      <fieldset className="calculation-fields" disabled={loading} aria-busy={loading}>
      <header className="app-header">
        <div className="brand-mark brand-mark--cube"><Box size={28} strokeWidth={2.2} /></div>
        <div><span className="eyebrow">LOAD PLANNING</span><h1>装柜方案助手</h1></div>
        <div className="header-status"><ShieldCheck size={16} /><span>草稿仅存本机</span><button type="button" className="header-action" onClick={() => setShowModelSettings(true)}><Settings2 size={16} />模型配置</button><button type="button" className="icon-button" aria-label="清除本地草稿" title="清除本地草稿" onClick={clearDraft}><Trash2 size={15} /></button></div>
      </header>

      <div className="input-workspace">
        <section className="planning-brief" aria-labelledby="planning-brief-heading">
          <img className="planning-banner" src={voyageBanner} alt="一帆风顺，满载启航海运横幅" />
          <div className="planning-detail">
            <div className="planning-copy">
              <span className="planning-kicker">LOAD PLANNING</span>
              <h2 id="planning-brief-heading">选择柜型，录入货物，生成可执行装柜方案。</h2>
            </div>
            <p className="planning-note">尺寸、重量与装载规则全程同步校验</p>
          </div>
        </section>
        <ContainerPicker presets={presets} selected={container} onSelect={setContainer} />
        <section className="saved-orders" aria-label="本机订单">
          <div><strong>本机订单</strong><button type="button" onClick={saveCurrentOrder}>保存当前订单</button></div>
          {savedOrders.length > 0 && <select aria-label="恢复本机订单" defaultValue="" onChange={(event) => { const order = savedOrders.find((item) => item.id === event.target.value); if (!order) return; setContainer(order.container); setCargoItems(order.cargoItems); setItemGapCm(order.itemGapCm); setClearanceCm(order.clearanceCm); setResult(null); setError(null); trackAnalyticsEvent("order_restored", { order_id: order.id }); }}><option value="">选择已保存订单</option>{savedOrders.map((order) => <option value={order.id} key={order.id}>{order.name} · {new Date(order.savedAt).toLocaleDateString("zh-CN")}</option>)}</select>}
        </section>
        <CargoTable
          rows={cargoItems}
          onChange={setCargoItems}
          onLoadPreset={loadPreset}
          onDownloadTemplate={() => downloadCargoTemplate().catch((reason: Error) => setError(reason.message))}
          onImportFile={(file) => {
            if (importing) return;
            setImporting(true); setImportReport(null); setError(null);
            readCargoExcelReport(file)
              .then(report => { setImportReport(report); if (report.issues.length) trackAnalyticsEvent('cargo_excel_import_failed', { issue_count: report.issues.length }); })
              .catch(() => setError('无法读取 Excel，请确认是有效的 .xlsx 文件且不超过 10 MB，工作表不超过 10000 行、100 列。当前清单未改变。'))
              .finally(() => setImporting(false));
          }}
        />
        {importing && <p role="status">正在读取 Excel，当前清单保持不变…</p>}
        {importReport && <section className="import-report" aria-label="Excel 数据质量报告">
          <h2>{importReport.issues.length ? `发现 ${importReport.issues.length} 项问题，尚未导入` : '导入预览：检查通过'}</h2>
          <p>仅读取首个工作表。当前清单在应用前不会改变。</p>
          {importReport.conversions.length > 0 && <p>单位换算：{importReport.conversions.join('；')}</p>}
          {importReport.issues.length > 0 ? <>
            <p>请按下列行号和列名修正原 Excel，再点击“导入 Excel”重新选择文件；系统不会跳过错误货物。</p>
            <ul className="import-issues">{importReport.issues.map((issue, i) => <li key={i}>第 {issue.row} 行 · {issue.column}：{issue.message}</li>)}</ul>
          </> : <>
            <p>{importReport.rows.length} 种货物，共 {importReport.rows.reduce((sum, row) => sum + row.quantity, 0)} 件；尺寸统一为 cm，重量统一为 kg。</p>
            <button className="primary-outline-button" onClick={() => {
              setCargoItems(importReport.rows.map((row, index) => ({ ...row, id: `cargo_import_${Date.now()}_${index}` })));
              trackAnalyticsEvent('cargo_excel_imported', { cargo_types: importReport.rows.length, pieces: importReport.rows.reduce((sum, row) => sum + row.quantity, 0) });
              setImportReport(null); setError(null);
            }}>应用导入并替换清单</button>
          </>}
          <button className="text-button" onClick={() => setImportReport(null)}>关闭报告</button>
        </section>}

        <section className="section-block settings-block" aria-labelledby="settings-heading">
          <div className="section-heading"><div><span className="step-index">03</span><h2 id="settings-heading">计算设置</h2></div></div>
          <div className="settings-grid">
            <label><span>本次优先目标</span><select aria-label="本次优先目标" value={preferredProfile} onChange={(event) => setPreferredProfile(event.target.value as SolutionProfile)}>
              <option value="high_fill">高装载率</option>
              <option value="stable">优先稳定</option>
              <option value="easy">优先易操作</option>
            </select></label>
            <label><span>货物间隙</span><span className="unit-input"><input type="number" min="0" step="0.1" value={itemGapCm} onChange={(event) => setItemGapCm(Number(event.target.value))} /><i>cm</i></span></label>
            <label><span>柜体安全边距</span><span className="unit-input"><input type="number" min="0" step="0.1" value={clearanceCm} onChange={(event) => setClearanceCm(Number(event.target.value))} /><i>cm</i></span></label>
            <div className="setting-summary"><FileSpreadsheet size={18} /><span>尺寸按厘米录入，计算时使用整数毫米<br />{aiConfig.apiKey.trim() ? `AI 策略：${aiConfig.model}` : "未配置 AI 时使用本地算法"}</span></div>
          </div>
        </section>

        {cargoValidationIssues.length > 0 && <div className="form-error" role="alert">
          <strong>请先修正货物清单</strong>
          <ul>{cargoValidationIssues.map((issue, index) => <li key={`${issue.row}-${issue.field}-${index}`}>{issue.row ? <button type="button" className="issue-link" onClick={() => focusIssue(issue.row, issue.field)}>第 {issue.row} 种货物：{issue.field}{issue.message} · 定位修改</button> : `整单：${issue.field}${issue.message}`}</li>)}</ul>
        </div>}
        {settingsError && <div className="form-error" role="alert">{settingsError}</div>}
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="calculate-bar">
          <div><strong>{cargoItems.reduce((sum, item) => sum + item.quantity, 0)}</strong><span>件货物 · {container?.name ?? "读取柜型中"}</span></div>
          <button type="button" className="calculate-button" onClick={calculate} disabled={!container || loading || importing || Boolean(cargoValidationError || settingsError)}>
            {loading ? <LoaderCircle className="spin" size={19} /> : <Calculator size={19} />}
            {loading ? "正在计算" : calculationFailed ? "重试计算" : "生成装柜方案"}
          </button>
        </div>
      </div>
      </fieldset>
      {progress && <CalculationProgress {...progress} />}
    </main>
  );
}
