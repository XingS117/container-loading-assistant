import { Box, Calculator, FileSpreadsheet, LoaderCircle, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CargoTable } from "./components/CargoTable";
import { ContainerPicker } from "./components/ContainerPicker";
import { SolutionWorkspace, recommendProfile } from "./components/SolutionWorkspace";
import { OrderHistory } from './components/OrderHistory';
import { ModelSettings } from "./components/ModelSettings";
import { CalculationProgress } from './components/CalculationProgress';
import voyageBanner from "./assets/voyage-banner.jpg";
import { CalculationError, getContainerPresets, packOrder, testAIConnection, type CalculationPhase } from "./lib/api";
import { loadAIConfig, saveAIConfig } from "./lib/aiConfig";
import { createCargo, validateCargo, validateCargoIssues, validateCalculationSettings } from "./lib/cargo";
import { cloneCargoPreset, COMMON_CARGO_PRESETS } from "./lib/cargoPresets";
import { downloadCargoTemplate, readCargoExcelReport, type ExcelReport } from "./lib/excel";
import { trackAnalyticsEvent } from "./lib/analytics";
import { loadSavedOrders, saveOrder, deleteSavedOrder, updateSavedSelection, type SavedOrder, type SavedWorkspace } from "./lib/orderHistory";
import type { AIModelConfig, CargoInput, CargoPreset, ContainerSpec, PackResponse, SolutionProfile } from "./types";

const STORAGE_KEY = "container-loading-assistant-draft-v1";

interface Draft {
  containerId: string;
  container: ContainerSpec;
  cargoItems: CargoInput[];
  itemGapCm: number;
  clearanceCm: number;
  activeSavedId?: string | null;
  orderName?: string;
  preferredProfile?: SolutionProfile;
  isExample?: boolean;
}

function loadDraft(): Partial<Draft> {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export default function App() {
  const [boot] = useState(() => {
    const draft = loadDraft();
    const restored = loadSavedOrders().find(order => order.id === draft.activeSavedId && order.response);
    return {draft,restored};
  });
  const draft = boot.restored ?? boot.draft;
  const [presets, setPresets] = useState<ContainerSpec[]>([]);
  const [container, setContainer] = useState<ContainerSpec | null>(boot.restored?.container ?? null);
  const [cargoItems, setCargoItems] = useState<CargoInput[]>(draft.cargoItems?.length ? draft.cargoItems : [createCargo("SKU-001")]);
  const [itemGapCm, setItemGapCm] = useState(draft.itemGapCm ?? 0);
  const [clearanceCm, setClearanceCm] = useState(draft.clearanceCm ?? 0);
  const [preferredProfile, setPreferredProfile] = useState<SolutionProfile>(draft.preferredProfile ?? "high_fill");
  const [orderName, setOrderName] = useState(boot.restored?.name ?? boot.draft.orderName ?? `订单 ${new Date().toLocaleDateString('zh-CN')}`);
  const [activeSavedId, setActiveSavedId] = useState<string | null>(boot.restored?.id ?? null);
  const [workspace, setWorkspace] = useState<SavedWorkspace | undefined>(boot.restored?.workspace);
  const [workspaceKey, setWorkspaceKey] = useState(0);
  const [historyMessage, setHistoryMessage] = useState(boot.restored ? '已恢复上次保存的完整方案。历史快照未按当前规则重新复核。' : '');
  const [draftError, setDraftError] = useState('');
  const [aiConfig, setAIConfig] = useState<AIModelConfig>(loadAIConfig);
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [result, setResult] = useState<PackResponse | null>(boot.restored?.response ?? null);
  const [restoredResult,setRestoredResult] = useState(Boolean(boot.restored?.response));
  const [loading, setLoading] = useState(false);
  const calculationPending = useRef(false);
  const [progress, setProgress] = useState<{phase: CalculationPhase; startedAt: number} | null>(null);
  const [calculationFailed, setCalculationFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedOrders, setSavedOrders] = useState(() => loadSavedOrders());
  const [importReport, setImportReport] = useState<ExcelReport | null>(null);
  const [importing, setImporting] = useState(false);
  const [exampleLoaded, setExampleLoaded] = useState(false);
  const inputFlow = useRef({id:crypto.randomUUID(),opened:false,completed:false});
  const [isExample,setIsExample] = useState(boot.restored?.response?.analytics_is_example ?? boot.draft.isExample ?? !boot.draft.cargoItems?.length);
  const cargoValidationError = validateCargo(cargoItems);
  const cargoValidationIssues = validateCargoIssues(cargoItems);
  const settingsError = validateCalculationSettings(container, itemGapCm, clearanceCm);
  const focusIssue = (row: number, field: string) => {
    const inputs = document.querySelector(`[data-cargo-row="${row}"]`)?.querySelectorAll<HTMLInputElement>('input');
    const input = Array.from(inputs ?? []).find(el => el.getAttribute('aria-label')?.startsWith(field === '货物代号' ? '货物代号或名称 ' : `${field} `));
    input?.scrollIntoView?.({ block: 'center', behavior: 'smooth' }); input?.focus();
  };

  useEffect(() => {
    if (result) { inputFlow.current={id:crypto.randomUUID(),opened:false,completed:false}; return; }
    if (container && !showModelSettings && !inputFlow.current.opened) {
      inputFlow.current.opened=true;
      trackAnalyticsEvent('pack_input_started',{input_id:inputFlow.current.id,source:boot.draft.cargoItems?.length?'draft':'new',is_example:isExample});
    }
  },[result,container,showModelSettings]);

  useEffect(() => {
    getContainerPresets()
      .then((items) => {
        setPresets(items);
        const selected = draft.container?.id === "custom"
          ? draft.container
          : items.find((item) => item.id === boot.draft.containerId) ?? items[0];
        setContainer(current => current ?? selected ?? null);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!container) return;
    const nextDraft: Draft = { containerId: container.id, container, cargoItems, itemGapCm, clearanceCm, activeSavedId, orderName, preferredProfile, isExample };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(nextDraft)); setDraftError(''); }
    catch { setDraftError('草稿及自动恢复位置未保存，请释放本机空间；当前页面仍可使用。'); }
  }, [container, cargoItems, itemGapCm, clearanceCm, activeSavedId, orderName, preferredProfile, isExample]);

  const persistOrder = (nextContainer: ContainerSpec, nextResult: PackResponse | undefined, nextWorkspace: SavedWorkspace | undefined, source: SavedOrder['source']) => {
    try {
      const saved = saveOrder({name:orderName.trim() || '未命名订单',container:nextContainer,cargoItems,itemGapCm,clearanceCm,preferredProfile,response:nextResult,workspace:nextWorkspace,source});
      setSavedOrders(loadSavedOrders());
      setActiveSavedId(nextResult ? saved.id : null);
      setHistoryMessage(`已保存到本机 · ${new Date(saved.savedAt).toLocaleTimeString('zh-CN')} · ${nextResult ? '完整方案版本' : '输入清单'}`);
    } catch (reason) {
      setActiveSavedId(null);
      setHistoryMessage(reason instanceof Error ? reason.message : '本次未保存，当前页面仍保留。');
    }
  };

  const workspaceChanged = (next: SavedWorkspace, reason: 'selection' | 'adjustment' | 'restore') => {
    setWorkspace(next);
    if (!container || !result) return;
    if (reason === 'selection') {
      if (!activeSavedId) return;
      try { updateSavedSelection(activeSavedId,next.selectedProfile); setSavedOrders(loadSavedOrders()); }
      catch (error) { setHistoryMessage(error instanceof Error ? error.message : '方案选择未保存'); }
    } else persistOrder(container,result,next,reason);
  };

  const calculateFor = async (nextContainer: ContainerSpec, lockedPlacements: import("./types").Placement[] = []) => {
    if (calculationPending.current) return;
    const validationError = validateCargo(cargoItems);
    if (validationError) throw new Error(validationError);
    const nextSettingsError = validateCalculationSettings(nextContainer, itemGapCm, clearanceCm);
    if (nextSettingsError) throw new Error(nextSettingsError);
    calculationPending.current = true;
    const startedAt = Date.now();
    const mode = result ? 'recalculate' : 'initial';
    const attemptId=crypto.randomUUID();
    const context={attempt_id:attemptId,input_id:result?.analytics_input_id ?? inputFlow.current.id,is_example:isExample};
    if (mode==='initial' && !inputFlow.current.completed) {
      inputFlow.current.completed=true;
      trackAnalyticsEvent('pack_input_completed',{...context,cargo_types:cargoItems.length,pieces:cargoItems.reduce((sum,item)=>sum+item.quantity,0),preferred_profile:preferredProfile});
    }
    let lastPhase: CalculationPhase='submitting';
    setProgress({phase:'submitting', startedAt});
    setCalculationFailed(false);
    setLoading(true);
    setError(null);
    trackAnalyticsEvent("pack_calculation_started", { ...context, mode, cargo_types: cargoItems.length, pieces: cargoItems.reduce((sum, item) => sum + item.quantity, 0), preferred_profile: preferredProfile });
    try {
      const requestContainer = { ...nextContainer, clearance_mm: Math.round(clearanceCm * 10) };
      const nextResult = {...await packOrder(requestContainer, cargoItems, itemGapCm, aiConfig, preferredProfile, lockedPlacements, phase => {if(phase!=='reading')lastPhase=phase;setProgress({phase, startedAt});}),analytics_attempt_id:attemptId,analytics_input_id:context.input_id,analytics_is_example:isExample};
      if (!nextResult.solutions.length) throw new CalculationError('服务未返回装柜方案，清单和原方案已保留，请稍后重试。', 'service', 'EMPTY_SOLUTIONS');
      setContainer(requestContainer);
      const nextWorkspace: SavedWorkspace = {selectedProfile:recommendProfile(nextResult),solutionOverrides:{},lockedCargoIds:[]};
      setWorkspace(nextWorkspace);
      setWorkspaceKey(key => key + 1);
      setResult(nextResult);
      setRestoredResult(false);
      persistOrder(requestContainer,nextResult,nextWorkspace,'calculation');
      trackAnalyticsEvent("pack_solutions_generated", { ...context, mode, elapsed_ms: Date.now() - startedAt, cargo_types: cargoItems.length, pieces: cargoItems.reduce((sum, item) => sum + item.quantity, 0), recommended_profile: nextResult.recommended_profile ?? preferredProfile,budget_fallback:nextResult.solutions.some(s=>s.assessment?.status==='budget_fallback') });
    } catch (reason) {
      setCalculationFailed(true);
      trackAnalyticsEvent('pack_calculation_failed', { ...context, mode, elapsed_ms: Date.now() - startedAt, category: reason instanceof CalculationError ? reason.category : 'service', reason: reason instanceof CalculationError ? reason.code : 'UNKNOWN',last_phase:lastPhase });
      if(reason instanceof CalculationError && reason.category==='timeout') trackAnalyticsEvent('pack_calculation_timeout',{...context,mode,elapsed_ms:Date.now()-startedAt,reason:reason.code,last_phase:lastPhase});
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
      return false;
    }
    const hintedContainer = presets.find(
      (item) =>
        item.name === preset.containerHint
        || item.id.toLowerCase() === preset.containerHint.toLowerCase(),
    );
    if (hintedContainer) setContainer(hintedContainer);
    setCargoItems(cloneCargoPreset(preset));
    setIsExample(true);
    setExampleLoaded(false);
    trackAnalyticsEvent("cargo_preset_loaded", { preset: preset.id });
    setResult(null);
    setActiveSavedId(null); setWorkspace(undefined);
    setError(null);
    return true;
  };

  const loadExample = () => {
    const example = COMMON_CARGO_PRESETS.find(p => p.id === 'common-abc');
    if (!example || !loadPreset(example)) return;
    setItemGapCm(0); setClearanceCm(0); setPreferredProfile('stable');
    setOrderName('ABC 测试示例'); setExampleLoaded(true); setImportReport(null); setCalculationFailed(false);
  };

  const clearDraft = () => {
    try { localStorage.removeItem(STORAGE_KEY); } catch { setDraftError('无法清除本机草稿，当前页面已重置。'); }
    setActiveSavedId(null); setWorkspace(undefined);
    setCargoItems([createCargo("SKU-001")]);
    setIsExample(true);
    setContainer(presets[0] ?? null);
    setItemGapCm(0);
    setClearanceCm(0);
    setError(null);
  };

  const saveCurrentOrder = () => {
    if (!container) return;
    persistOrder(container,result ?? undefined,result ? workspace : undefined,result ? 'manual' : 'input');
  };

  const restoreOrder = (order: SavedOrder, copy: boolean) => {
    setIsExample(order.response?.analytics_is_example ?? false);
    setRestoredResult(!copy && Boolean(order.response)); setExampleLoaded(false);
    setContainer(order.container); setCargoItems(order.cargoItems); setItemGapCm(order.itemGapCm); setClearanceCm(order.clearanceCm);
    setPreferredProfile(order.preferredProfile ?? 'high_fill'); setOrderName(copy ? `${order.name} 副本` : order.name);
    setResult(copy ? null : order.response ?? null); setWorkspace(copy ? undefined : order.workspace); setWorkspaceKey(key => key + 1);
    setActiveSavedId(!copy && order.response ? order.id : null); setError(null); setCalculationFailed(false); setImportReport(null);
    setHistoryMessage(copy ? '已复制输入为新订单，原版本保留；请重新计算。' : order.response ? '已恢复完整历史方案，未按当前规则重新复核。' : '已恢复历史输入清单。');
    trackAnalyticsEvent(copy ? 'order_copied' : 'order_restored',{order_id:order.id});
  };
  const removeOrder = (order: SavedOrder) => {
    if (!window.confirm(`删除“${order.name}”的这个本机版本？当前页面布局不会改变。`)) return;
    try {
      deleteSavedOrder(order.id); setSavedOrders(loadSavedOrders());
      if (activeSavedId === order.id) setActiveSavedId(null);
      setHistoryMessage(activeSavedId === order.id ? '当前版本已删除，页面仍保留；如需刷新恢复，请重新保存。' : '历史版本已删除。');
    } catch (error) { setHistoryMessage(error instanceof Error ? error.message : '删除失败'); }
  };
  const historyPanel = <OrderHistory orders={savedOrders} name={orderName} onNameChange={setOrderName} onSave={saveCurrentOrder} onRestore={restoreOrder} onDelete={removeOrder} hasResult={Boolean(result)} message={[historyMessage,draftError].filter(Boolean).join(' ')} />;

  if (result && container) {
    return <><fieldset className="calculation-fields" disabled={loading} aria-busy={loading}><SolutionWorkspace key={workspaceKey} response={result} container={container} presets={presets} cargoItems={cargoItems} itemGapCm={itemGapCm} initialWorkspace={workspace} restored={restoredResult} onWorkspaceChange={workspaceChanged} historyPanel={historyPanel} onBack={() => { trackAnalyticsEvent("pack_edit_input",{attempt_id:result.analytics_attempt_id ?? 'legacy',is_example:isExample}); setResult(null); setActiveSavedId(null); setWorkspace(undefined); setCalculationFailed(false); }} onRecalculate={calculateFor} recalculating={loading} /></fieldset>{progress && <CalculationProgress {...progress} />}</>;
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
        <section className="quick-start" aria-label="快速开始">
          <div><strong>第一次使用？先看一个完整示例</strong><p>40HQ · 3 种整托 · 63 托。载入后核对清单，点击“生成装柜方案”，再比较、调整或打印；无需先配置模型。</p></div>
          <button type="button" className="primary-outline-button" disabled={!presets.some(p=>p.id==='40hq')} onClick={loadExample}>载入完整示例</button>
          {exampleLoaded && <p className="example-note" role="status">已载入用户测试案例：ZT1 150 kg、ZT2 280 kg、ZT3 400 kg。顶部承重 500 kg、最多两层等为示例假设，不代表真实发货或包装认证，请在实际使用前核实。</p>}
        </section>
        <ContainerPicker presets={presets} selected={container} onSelect={setContainer} />
        {historyPanel}
        <CargoTable
          rows={cargoItems}
          onChange={rows=>{setCargoItems(rows);if(!exampleLoaded)setIsExample(false);}}
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
              setIsExample(false); setExampleLoaded(false);
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
