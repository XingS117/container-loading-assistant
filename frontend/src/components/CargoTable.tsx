import { Download, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { createCargo } from "../lib/cargo";
import { COMMON_CARGO_PRESETS } from "../lib/cargoPresets";
import type { CargoInput, CargoPreset } from "../types";

interface Props {
  rows: CargoInput[];
  onChange: (rows: CargoInput[]) => void;
  onLoadPreset?: (preset: CargoPreset) => void;
  onImportFile?: (file: File) => void;
  onDownloadTemplate?: () => void;
}

type NumericKey =
  | "length_cm"
  | "width_cm"
  | "height_cm"
  | "weight_kg"
  | "quantity"
  | "max_layers"
  | "max_top_load_kg"
  | "unload_order";

export function CargoTable({ rows, onChange, onLoadPreset, onImportFile, onDownloadTemplate }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [numericDrafts, setNumericDrafts] = useState<Record<string, string>>({});
  const [presetOpen, setPresetOpen] = useState(false);
  const update = <K extends keyof CargoInput>(index: number, key: K, value: CargoInput[K]) => {
    onChange(rows.map((row, rowIndex) => (rowIndex === index ? { ...row, [key]: value } : row)));
  };
  const draftKey = (rowId: string, key: NumericKey) => `${rowId}:${key}`;
  const numericValue = (row: CargoInput, key: NumericKey) => (
    numericDrafts[draftKey(row.id, key)] ?? (row[key] == null ? "" : String(row[key]))
  );
  const updateNumber = (index: number, key: NumericKey, value: string) => {
    const row = rows[index];
    setNumericDrafts((current) => ({ ...current, [draftKey(row.id, key)]: value }));
    if (value === "") {
      if (key === "weight_kg") update(index, key, null);
      return;
    }
    const number = Number(value);
    if (Number.isFinite(number)) update(index, key, number as CargoInput[NumericKey]);
  };
  const clearNumericDraft = (rowId: string, key: NumericKey) => {
    const keyToClear = draftKey(rowId, key);
    setNumericDrafts((current) => {
      const { [keyToClear]: _, ...remaining } = current;
      return remaining;
    });
  };

  return (
    <section className="section-block cargo-section" aria-labelledby="cargo-heading">
      <div className="section-heading">
        <div>
          <span className="step-index">02</span>
          <h2 id="cargo-heading">货物清单</h2>
        </div>
        <div className="cargo-actions">
          {onLoadPreset && <div className="preset-picker">
            <button type="button" className="compact-button" onClick={() => setPresetOpen((open) => !open)} aria-expanded={presetOpen}>
              常见产品规格
            </button>
            {presetOpen && <div className="preset-menu" role="menu">
              {(["组合", "单品"] as const).map((kind) => (
                <div key={kind} className="preset-group">
                  <strong>{kind}</strong>
                  {COMMON_CARGO_PRESETS.filter((preset) => preset.kind === kind).map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        onLoadPreset(preset);
                        setPresetOpen(false);
                      }}
                    >
                      <span>{preset.label}</span>
                      <small>{preset.containerHint} · {preset.description}</small>
                    </button>
                  ))}
                </div>
              ))}
            </div>}
          </div>}
          {onDownloadTemplate && <button type="button" className="compact-button" onClick={onDownloadTemplate}><Download size={15} />模板</button>}
          {onImportFile && <><button type="button" className="compact-button" onClick={() => fileInputRef.current?.click()}><Upload size={15} />导入 Excel</button><input ref={fileInputRef} className="visually-hidden" type="file" accept=".xlsx" aria-label="选择 Excel 文件" onChange={(event) => { const file = event.target.files?.[0]; if (file) onImportFile(file); event.target.value = ""; }} /></>}
          <span className="section-note">{rows.length} / 30 种</span>
        </div>
      </div>

      <div className="cargo-table" role="table" aria-label="货物清单">
        <div className="cargo-table-head" role="row">
          <span>货物</span><span>类型</span><span>长 × 宽 × 高 (cm)</span><span>单重</span><span>数量</span><span>摆放</span><span>叠放</span><span>约束</span><span />
        </div>
        {rows.map((row, index) => (
          <div className="cargo-row" role="row" key={row.id}>
            <div className="cargo-identity cargo-field" data-label="货物">
              <input aria-label={`SKU ${index + 1}`} value={row.sku} onChange={(event) => update(index, "sku", event.target.value)} />
              <input aria-label={`货物名称 ${index + 1}`} value={row.name} onChange={(event) => update(index, "name", event.target.value)} />
            </div>
            <label className="cargo-field"><span className="field-title">类型</span><select aria-label={`货物类型 ${row.sku}`} value={row.kind} onChange={(event) => {
              const next = event.target.value as CargoInput["kind"];
              if (next === "pallet") {
                // 一次更新多个字段：update() 基于闭包旧 rows，连续多次调用会互相覆盖
                onChange(rows.map((item, rowIndex) => (
                  rowIndex === index
                    ? { ...item, kind: "pallet", stackable: false, max_top_load_kg: 500 }
                    : item
                )));
              } else {
                update(index, "kind", next);
              }
            }}>
              <option value="carton">散箱</option>
              <option value="pallet">整托</option>
            </select></label>
            <div className="dimension-inputs cargo-field" data-label="长 × 宽 × 高 (cm)">
              {(["length_cm", "width_cm", "height_cm"] as const).map((key, dimensionIndex) => (
                <input key={key} type="number" min="0.1" step="0.1" aria-label={`${["长", "宽", "高"][dimensionIndex]} ${row.sku}`} value={numericValue(row, key)} onChange={(event) => updateNumber(index, key, event.target.value)} onBlur={() => clearNumericDraft(row.id, key)} />
              ))}
            </div>
            <label className="unit-input cargo-field"><span className="field-title">单重</span><input type="number" min="0.01" step="0.01" aria-label={`单重 ${row.sku}`} placeholder={row.weight_kg == null ? "需补充" : undefined} value={numericValue(row, "weight_kg")} onChange={(event) => updateNumber(index, "weight_kg", event.target.value)} onBlur={() => clearNumericDraft(row.id, "weight_kg")} /><span className="unit-suffix">kg</span>{row.weight_kg == null && <span className="pending-weight">需补充重量</span>}</label>
            <label className="cargo-field"><span className="field-title">数量</span><input className="quantity-input" type="number" min="1" step="1" aria-label={`数量 ${row.sku}`} value={numericValue(row, "quantity")} onChange={(event) => updateNumber(index, "quantity", event.target.value)} onBlur={() => clearNumericDraft(row.id, "quantity")} /></label>
            <label className="cargo-field"><span className="field-title">摆放</span><select aria-label={`允许摆放 ${row.sku}`} value={row.orientation_mode} onChange={(event) => update(index, "orientation_mode", event.target.value as CargoInput["orientation_mode"])}>
              <option value="upright">保持正放</option>
              <option value="side">允许侧放</option>
              <option value="any">任意朝向</option>
            </select></label>
            <div className="stack-controls cargo-field" data-label="叠放">
              <label className="toggle-label"><input type="checkbox" checked={row.stackable} onChange={(event) => update(index, "stackable", event.target.checked)} />可叠</label>
              {row.stackable && <label className="mini-input">层<input type="number" min="1" max="100" aria-label={`最大层数 ${row.sku}`} value={numericValue(row, "max_layers")} onChange={(event) => updateNumber(index, "max_layers", event.target.value)} onBlur={() => clearNumericDraft(row.id, "max_layers")} /></label>}
              {(row.stackable || row.kind === "pallet") && <label className="mini-input top-load-input">承重<input type="number" min="0" step="0.1" aria-label={`顶部承重 ${row.sku}`} value={numericValue(row, "max_top_load_kg")} onChange={(event) => updateNumber(index, "max_top_load_kg", event.target.value)} onBlur={() => clearNumericDraft(row.id, "max_top_load_kg")} /><span>kg</span></label>}
            </div>
            <div className="constraint-flags cargo-field" data-label="约束">
              <label className="mini-input">卸货顺序<input type="number" min="0" aria-label={`卸货顺序 ${row.sku}`} value={numericValue(row, "unload_order")} onChange={(event) => updateNumber(index, "unload_order", event.target.value)} onBlur={() => clearNumericDraft(row.id, "unload_order")} title="数字小者先卸，后卸的货物先装进柜头（0=不指定）" /></label>
              <label><input type="checkbox" checked={row.fragile} onChange={(event) => update(index, "fragile", event.target.checked)} />易碎</label>
              <label><input type="checkbox" checked={row.must_load} onChange={(event) => update(index, "must_load", event.target.checked)} />必装</label>
            </div>
            <button type="button" className="icon-button danger" aria-label={`删除 ${row.sku}`} title="删除货物" onClick={() => onChange(rows.filter((_, rowIndex) => rowIndex !== index))}>
              <Trash2 size={17} />
            </button>
          </div>
        ))}
      </div>

      <button type="button" className="add-cargo-button" onClick={() => onChange([...rows, createCargo()])} disabled={rows.length >= 30}>
        <Plus size={17} /> 添加货物
      </button>
    </section>
  );
}
