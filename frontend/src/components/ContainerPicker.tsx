import { Box, PencilRuler, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";

import type { ContainerSpec } from "../types";

interface Props {
  presets: ContainerSpec[];
  selected: ContainerSpec | null;
  onSelect: (container: ContainerSpec) => void;
}

function dimensionText(container: ContainerSpec): string {
  return `${(container.inner_length_mm / 1000).toFixed(2)} × ${(container.inner_width_mm / 1000).toFixed(2)} × ${(container.inner_height_mm / 1000).toFixed(2)} m`;
}

export function ContainerPicker({ presets, selected, onSelect }: Props) {
  const [custom, setCustom] = useState<ContainerSpec>({
    id: "custom",
    name: "自定义柜型",
    inner_length_mm: 12000,
    inner_width_mm: 2350,
    inner_height_mm: 2690,
    door_width_mm: 2340,
    door_height_mm: 2580,
    max_payload_g: 28000000,
    clearance_mm: 0,
  });
  const updateCustom = (key: keyof ContainerSpec, value: number) => {
    const next = { ...custom, [key]: value };
    setCustom(next);
    onSelect(next);
  };
  useEffect(() => {
    if (selected?.id === "custom") setCustom(selected);
  }, [selected]);

  return (
    <section className="section-block" aria-labelledby="container-heading">
      <div className="section-heading">
        <div>
          <span className="step-index">01</span>
          <h2 id="container-heading">选择柜型</h2>
        </div>
        <span className="section-note"><PencilRuler size={15} /> 内尺寸可核对</span>
      </div>
      <div className="container-options" role="list">
        {presets.map((container) => (
          <button
            type="button"
            className={`container-option ${selected?.id === container.id ? "is-selected" : ""}`}
            key={container.id}
            onClick={() => onSelect(container)}
            aria-pressed={selected?.id === container.id}
          >
            <Box size={19} aria-hidden="true" />
            <span className="container-name">{container.name}</span>
            <span className="container-dimensions">{dimensionText(container)}</span>
            <span className="container-payload">载重 {(container.max_payload_g / 1_000_000).toFixed(1)} t</span>
          </button>
        ))}
        <button
          type="button"
          className={`container-option ${selected?.id === "custom" ? "is-selected" : ""}`}
          onClick={() => onSelect(custom)}
          aria-pressed={selected?.id === "custom"}
        >
          <SlidersHorizontal size={19} aria-hidden="true" />
          <span className="container-name">自定义</span>
          <span className="container-dimensions">录入实际柜内尺寸</span>
          <span className="container-payload">柜门与载重可单独设置</span>
        </button>
      </div>
      {selected?.id === "custom" && (
        <div className="custom-container-editor">
          {([
            ["inner_length_mm", "柜内长", "cm", 10],
            ["inner_width_mm", "柜内宽", "cm", 10],
            ["inner_height_mm", "柜内高", "cm", 10],
            ["door_width_mm", "柜门宽", "cm", 10],
            ["door_height_mm", "柜门高", "cm", 10],
            ["max_payload_g", "最大载重", "kg", 1000],
          ] as const).map(([key, label, unit, multiplier]) => (
            <label key={key}>
              <span>{label}</span>
              <span className="unit-input">
                <input type="number" min="1" step="0.1" value={custom[key] / multiplier} onChange={(event) => updateCustom(key, Math.round(Number(event.target.value) * multiplier))} />
                <i>{unit}</i>
              </span>
            </label>
          ))}
        </div>
      )}
    </section>
  );
}
