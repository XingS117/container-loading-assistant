import { Box, Calculator, FileSpreadsheet, LoaderCircle, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { CargoTable } from "./components/CargoTable";
import { ContainerPicker } from "./components/ContainerPicker";
import { SolutionWorkspace } from "./components/SolutionWorkspace";
import voyageBanner from "./assets/voyage-banner.jpg";
import { getContainerPresets, packOrder } from "./lib/api";
import { createCargo, validateCargo } from "./lib/cargo";
import { cloneCargoPreset } from "./lib/cargoPresets";
import { downloadCargoTemplate, readCargoExcel } from "./lib/excel";
import { trackAnalyticsEvent } from "./lib/analytics";
import type { CargoInput, CargoPreset, ContainerSpec, PackResponse } from "./types";

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
  const [result, setResult] = useState<PackResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cargoValidationError = validateCargo(cargoItems);

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

  const calculateFor = async (nextContainer: ContainerSpec) => {
    const validationError = validateCargo(cargoItems);
    if (validationError) throw new Error(validationError);
    setLoading(true);
    setError(null);
    try {
      const requestContainer = { ...nextContainer, clearance_mm: Math.round(clearanceCm * 10) };
      const nextResult = await packOrder(requestContainer, cargoItems, itemGapCm);
      setContainer(nextContainer);
      setResult(nextResult);
      trackAnalyticsEvent("pack_solutions_generated");
    } finally {
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

  const loadPreset = (preset: CargoPreset) => {
    if (cargoItems.length > 0 && !window.confirm("加载常见规格将替换当前货物清单，是否继续？")) {
      return;
    }
    setCargoItems(cloneCargoPreset(preset));
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

  if (result && container) {
    return <SolutionWorkspace response={result} container={container} presets={presets} cargoItems={cargoItems} onBack={() => setResult(null)} onRecalculate={calculateFor} recalculating={loading} />;
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-mark brand-mark--cube"><Box size={28} strokeWidth={2.2} /></div>
        <div><span className="eyebrow">LOAD PLANNING</span><h1>装柜方案助手</h1></div>
        <div className="header-status"><ShieldCheck size={16} /><span>草稿仅存本机</span><button type="button" className="icon-button" aria-label="清除本地草稿" title="清除本地草稿" onClick={clearDraft}><Trash2 size={15} /></button></div>
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
        <CargoTable
          rows={cargoItems}
          onChange={setCargoItems}
          onLoadPreset={loadPreset}
          onDownloadTemplate={() => downloadCargoTemplate().catch((reason: Error) => setError(reason.message))}
          onImportFile={(file) => {
            readCargoExcel(file)
              .then((rows) => { setCargoItems(rows); setError(null); })
              .catch((reason: Error) => setError(reason.message));
          }}
        />

        <section className="section-block settings-block" aria-labelledby="settings-heading">
          <div className="section-heading"><div><span className="step-index">03</span><h2 id="settings-heading">计算设置</h2></div></div>
          <div className="settings-grid">
            <label><span>货物间隙</span><span className="unit-input"><input type="number" min="0" step="0.1" value={itemGapCm} onChange={(event) => setItemGapCm(Number(event.target.value))} /><i>cm</i></span></label>
            <label><span>柜体安全边距</span><span className="unit-input"><input type="number" min="0" step="0.1" value={clearanceCm} onChange={(event) => setClearanceCm(Number(event.target.value))} /><i>cm</i></span></label>
            <div className="setting-summary"><FileSpreadsheet size={18} /><span>尺寸按厘米录入，计算时使用整数毫米</span></div>
          </div>
        </section>

        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="calculate-bar">
          <div><strong>{cargoItems.reduce((sum, item) => sum + item.quantity, 0)}</strong><span>件货物 · {container?.name ?? "读取柜型中"}</span></div>
          <button type="button" className="calculate-button" onClick={calculate} disabled={!container || loading || Boolean(cargoValidationError)}>
            {loading ? <LoaderCircle className="spin" size={19} /> : <Calculator size={19} />}
            {loading ? "正在计算" : "生成装柜方案"}
          </button>
        </div>
      </div>
    </main>
  );
}
