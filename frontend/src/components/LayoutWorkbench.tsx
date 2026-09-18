import { useEffect, useReducer, useRef, useState } from "react";
import { LoadVisualizer } from "./LoadVisualizer";
import { reviewLayout } from "../lib/api";
import { orientationsFor } from "../lib/cargo";
import { constrainMove, editHistory, geometryError, relocateDraft } from "../lib/workbench";
import type { CargoInput, ContainerSpec, LayoutReviewResponse, Orientation, PackingSolution, Placement } from "../types";

interface Props {
  solution: PackingSolution;
  container: ContainerSpec;
  cargoItems: CargoInput[];
  itemGapCm: number;
  onApply: (solution: PackingSolution, lockedCargoIds: Set<string>) => void;
  initialLockedCargoIds?: Set<string>;
  onClose: () => void;
}

export function LayoutWorkbench({ solution, container, cargoItems, itemGapCm, onApply, onClose, initialLockedCargoIds }: Props) {
  const [history, dispatch] = useReducer(editHistory, { past: [], present: solution.placements, future: [] });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [locked, setLocked] = useState<Set<string>>(new Set(initialLockedCargoIds));
  const [wholeCargo, setWholeCargo] = useState(false);
  const [moving, setMoving] = useState(false);
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState(["0", "0", "0"]);
  const [review, setReview] = useState<{ placements: Placement[]; result: LayoutReviewResponse } | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [moveNotice, setMoveNotice] = useState("");
  const [checking, setChecking] = useState(false);
  const busyRef = useRef(false);
  const aliveRef = useRef(true);
  const [panel, setPanel] = useState<"cargo" | "inspect" | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const placements = history.present;
  const selected = placements.find(p => p.id === selectedId);
  const cargo = cargoItems.find(c => c.id === selected?.cargo_id);
  const currentReview = review?.placements === placements ? review.result : null;
  const invalidIds = currentReview?.errors.flatMap(e => e.placement_ids) ?? [];
  const dirty = JSON.stringify(placements) !== JSON.stringify(solution.placements) || [...locked].sort().join('|') !== [...(initialLockedCargoIds ?? [])].sort().join('|');
  const names = Object.fromEntries(cargoItems.map(c => [c.id, c.sku]));

  useEffect(() => {
    aliveRef.current = true;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { aliveRef.current = false; document.body.style.overflow = overflow; previous?.focus(); };
  }, []);

  useEffect(() => {
    setPosition(selected ? [selected.x_mm / 10, selected.y_mm / 10, selected.z_mm / 10].map(String) : ["0", "0", "0"]);
  }, [selected]);

  useEffect(() => {
    let active = true;
    setError("");
    const timer = window.setTimeout(() => {
      reviewLayout(container, cargoItems, placements, itemGapCm)
        .then(result => { if (active) setReview({ placements, result }); })
        .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "检查失败，请重试"); });
    }, 600);
    return () => { active = false; window.clearTimeout(timer); };
  }, [placements, container, cargoItems, itemGapCm]);

  const edit = async (next: Placement[], notice = '') => {
    if (busyRef.current) return;
    if (next === placements || JSON.stringify(next) === JSON.stringify(placements)) return;
    const issue = geometryError(next, container, itemGapCm * 10);
    if (issue) { setActionError(issue); return; }
    busyRef.current = true; setChecking(true); setActionError(""); setMoveNotice("");
    try {
      const result = await reviewLayout(container, cargoItems, next, itemGapCm);
      if (!aliveRef.current) return;
      if (!result.valid || !result.metrics) { setActionError(result.errors.map(e => e.message).join('；') || '校验未通过，已保留原位置'); return; }
      dispatch({ type: "edit", placements: next });
      setMoveNotice(notice);
    } catch (reason) {
      if (aliveRef.current) setActionError(`${reason instanceof Error ? reason.message : '检查失败'}，已保留原位置`);
    } finally {
      busyRef.current = false;
      if (aliveRef.current) setChecking(false);
    }
  };
  const move = (id: string, x: number, y: number, z?: number) => {
    const item = placements.find(p => p.id === id);
    if (!item) return;
    if (z !== undefined) { edit(constrainMove(placements, id, [x, y, z], wholeCargo, locked, container)); return; }
    const result = relocateDraft(placements, id, [x, y], wholeCargo, locked, container, itemGapCm * 10);
    if (result.error) { setActionError(result.error); return; }
    const count = result.placements.filter((p, i) => p.id !== id && !(wholeCargo && p.cargo_id === item.cargo_id) && p.z_mm !== placements[i].z_mm).length;
    edit(result.placements, `${result.snapped ? '已吸附对齐；' : ''}${count ? `搬移完成，原货位 ${count} 件货物已向下归位；可撤销整次操作` : '已自动落在柜底或承载面，可撤销'}`);
  };
  const rotate = (rotation: Orientation) => {
    if (!selected || !cargo || locked.has(cargo.id) || !orientationsFor(cargo.orientation_mode).includes(rotation)) return;
    const dims: Record<string, number> = { L: Math.round(cargo.length_cm * 10), W: Math.round(cargo.width_cm * 10), H: Math.round(cargo.height_cm * 10) };
    edit(placements.map(p => p.id !== selected.id ? p : { ...p, rotation, length_mm: dims[rotation[0]], width_mm: dims[rotation[1]], height_mm: dims[rotation[2]] }));
  };
  const previewMove = (id: string, x: number, y: number) => {
    const item = placements.find(p => p.id === id);
    if (!item) return null;
    const result = relocateDraft(placements, id, [x, y], wholeCargo, locked, container, itemGapCm * 10);
    return result.error ? null : result.placements.find(p => p.id === id) ?? null;
  };
  const close = () => { if (!dirty || window.confirm("放弃本次尚未应用的调整？")) onClose(); };
  const apply = () => {
    if (!currentReview?.valid || !currentReview.metrics) return;
    onApply({ ...solution, placements, metrics: currentReview.metrics, zones: currentReview.zones, identical_to: null,
      pros: ["人工调整已通过边界、碰撞、朝向、支撑和承重规则检查"],
      cons: ["几何校验不替代现场绑扎、运输动态稳定性和装卸可达性复核"],
      warnings: ["人工调整后请按新布局复核装载顺序和柜门操作空间"],
    }, locked);
  };

  return <div className="layout-workbench no-print" role="dialog" aria-modal="true" aria-label="装柜编辑工作台" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); setMoving(false); }
    if (event.key === "Tab") {
      const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(el => el.getClientRects().length);
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0]?.focus(); }
    }
  }}>
    <header className="workbench-header">
      <div><small>LAYOUT STUDIO · {solution.name}</small><h1>装柜编辑工作台</h1></div>
      <div className="workbench-actions">
        <button ref={closeRef} onClick={close}>返回方案</button>
        <button disabled={checking || !history.past.length} onClick={() => { setActionError(''); setMoveNotice(''); dispatch({ type: "undo" }); }}>撤销</button>
        <button disabled={checking || !history.future.length} onClick={() => { setActionError(''); setMoveNotice(''); dispatch({ type: "redo" }); }}>重做</button>
        <button disabled={checking || !dirty} onClick={() => edit(solution.placements)}>恢复原方案</button>
        <button className="workbench-apply" disabled={checking || !dirty || !currentReview?.valid || !currentReview.metrics} onClick={apply}>应用调整</button>
      </div>
    </header>
    <nav className="workbench-mode" aria-label="编辑操作">
      <button aria-pressed={!moving} onClick={() => setMoving(false)}>浏览 / 选择</button>
      <button aria-pressed={moving} onClick={() => setMoving(true)}>移动货物</button>
      <span>{moving ? "拖动自动贴边对齐并落位，保留设置间隙；原货位上层向下归位。Esc 取消" : "点击货物选中；拖动空白处转视角，滚轮缩放"}</span>
      <button className="workbench-panel-toggle" onClick={() => setPanel(panel === "cargo" ? null : "cargo")}>货物列表</button>
      <button className="workbench-panel-toggle" onClick={() => setPanel(panel === "inspect" ? null : "inspect")}>坐标 / 检查</button>
    </nav>
    <div className="workbench-body">
      <aside className={`workbench-cargo ${panel === "cargo" ? "is-open" : ""}`}>
        <h2>选择货物 <small>{placements.length} 件</small></h2>
        <input aria-label="搜索货物" placeholder="搜索货物名称" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="workbench-cargo-list">{placements.filter(p => (names[p.cargo_id] ?? p.cargo_id).toLowerCase().includes(search.toLowerCase())).slice(0, 300).map(p => <button key={p.id} aria-pressed={p.id === selectedId} onClick={() => { setSelectedId(p.id); setPanel(null); }}>
          {names[p.cargo_id]} · 第 {p.instance_index + 1} 件{locked.has(p.cargo_id) && <small>已锁定</small>}
        </button>)}</div>
        {placements.length > 300 && <p>列表最多显示 300 件，请搜索缩小范围，也可在图中选择。</p>}
      </aside>
      <div className="workbench-canvas">
        <LoadVisualizer container={container} solution={{ ...solution, placements, zones: [] }} cargoItems={cargoItems}
          selectedCargoId={selected?.cargo_id} selectedPlacementId={selectedId} onSelectPlacement={setSelectedId}
          onSelectCargo={id => setSelectedId(placements.find(p => p.cargo_id === id)?.id ?? null)}
          onMovePlacement={moving && !checking ? move : undefined} previewMove={previewMove} editing={moving && !checking} hideLegend lockedCargoIds={locked} invalidPlacementIds={invalidIds} />
        {(actionError || checking) && <div className="workbench-action-status" role="alert">{checking ? '正在校验承重和摆放规则…' : actionError}</div>}
        {moveNotice && !actionError && !checking && <div className="workbench-action-status" role="status">{moveNotice}</div>}
        <div className="workbench-caption">{selected ? `${names[selected.cargo_id]} · 第 ${selected.instance_index + 1} 件` : "先点击货物，再选择移动或输入坐标"} · 未应用草稿</div>
      </div>
      <aside className={`workbench-inspector ${panel === "inspect" ? "is-open" : ""}`}>
        <h2>位置与朝向</h2>
        {selected && cargo ? <>
          <fieldset disabled={checking} className="workbench-edit-fields">
          <p>{cargo.sku} · 第 {selected.instance_index + 1} 件</p>
          <label><input type="checkbox" checked={wholeCargo} onChange={e => setWholeCargo(e.target.checked)} />移动同 SKU 全部货物</label>
          <button onClick={() => setLocked(previous => { const next = new Set(previous); if (next.has(cargo.id)) next.delete(cargo.id); else next.add(cargo.id); return next; })}>{locked.has(cargo.id) ? "解锁该 SKU" : "锁定该 SKU"}</button>
          <label className="workbench-readonly-field">货物尺寸（不可修改）<input aria-label="货物尺寸（不可修改）" value={`${cargo.length_cm} × ${cargo.width_cm} × ${cargo.height_cm} cm`} readOnly /></label>
          <div className="workbench-nudge" role="group" aria-label="按货物尺寸平移">
            <button disabled={locked.has(cargo.id)} onClick={() => move(selected.id, selected.x_mm - selected.length_mm - itemGapCm * 10, selected.y_mm)}>向柜头移一件</button>
            <button disabled={locked.has(cargo.id)} onClick={() => move(selected.id, selected.x_mm + selected.length_mm + itemGapCm * 10, selected.y_mm)}>向柜门移一件</button>
            <button disabled={locked.has(cargo.id)} onClick={() => move(selected.id, selected.x_mm, selected.y_mm - selected.width_mm - itemGapCm * 10)}>左移一件宽</button>
            <button disabled={locked.has(cargo.id)} onClick={() => move(selected.id, selected.x_mm, selected.y_mm + selected.width_mm + itemGapCm * 10)}>右移一件宽</button>
            <button disabled={locked.has(cargo.id)} onClick={() => rotate(`${selected.rotation[1]}${selected.rotation[0]}${selected.rotation[2]}` as Orientation)}>水平旋转 90°</button>
            <button disabled={locked.has(cargo.id)} onClick={() => move(selected.id, selected.x_mm, selected.y_mm, container.clearance_mm ?? 0)}>放回底层</button>
          </div>
          <small>靠近柜壁或相邻货物约 5cm 内自动吸附，小件范围相应缩小，保留设置间隙。步距为当前长/宽加间隙。移开底层时原上层向下归位；精确 XYZ 不吸附，须有完整支撑。</small>
          <div className="workbench-coordinates">{["X 柜长 cm", "Y 柜宽 cm", "Z 高度 cm"].map((label, i) => <label key={label}>{label}<input aria-label={label} type="number" step="1" value={position[i]} disabled={locked.has(cargo.id)} onChange={e => setPosition(p => p.map((v, j) => i === j ? e.target.value : v))} /></label>)}</div>
          <button disabled={locked.has(cargo.id)} onClick={() => {
            if (position.slice(0, 2).some(v => !v.trim() || !Number.isFinite(Number(v)))) { setActionError('请输入有效的 X、Y 坐标'); return; }
            move(selected.id, Number(position[0]) * 10, Number(position[1]) * 10);
          }}>按 X / Y 自动落位</button>
          <button disabled={locked.has(cargo.id)} onClick={() => {
            if (position.some(v => !v.trim() || !Number.isFinite(Number(v)))) { setError("请输入有效坐标"); return; }
            move(selected.id, Number(position[0]) * 10, Number(position[1]) * 10, Number(position[2]) * 10);
          }}>预览坐标</button>
          <label>当前单件朝向<select aria-label="当前单件朝向" disabled={locked.has(cargo.id)} value={selected.rotation} onChange={e => rotate(e.target.value as Orientation)}>{orientationsFor(cargo.orientation_mode).map(r => <option key={r}>{r}</option>)}</select></label>
          <small>L=原长，W=原宽，H=原高；按柜长、柜宽、高度依次排列。仅显示货物允许的朝向。</small>
          </fieldset>
        </> : <p>请选择一件货物，或在左侧搜索。</p>}
        <h2>安全复核</h2>
        <p role="status">{error || (!currentReview ? "正在检查当前草稿…" : currentReview.valid ? "规则检查通过，可应用；现场仍需复核" : "当前草稿不能应用，请处理以下问题")}</p>
        {currentReview?.errors.map((issue, i) => <button className="workbench-issue" key={`${issue.code}-${i}`} onClick={() => setSelectedId(issue.placement_ids[0] ?? null)}>{issue.message}</button>)}
        {error && <button onClick={() => dispatch({ type: "edit", placements: [...placements] })}>重新检查</button>}
        <h2>调整前 → 当前</h2>
        <p>装入件数：{solution.metrics.loaded_pieces} → {placements.length}</p>
        <p>前后偏差：{solution.metrics.length_imbalance_pct}% → {currentReview?.metrics ? `${currentReview.metrics.length_imbalance_pct}%` : "待校验"}</p>
        <p>左右偏差：{solution.metrics.width_imbalance_pct}% → {currentReview?.metrics ? `${currentReview.metrics.width_imbalance_pct}%` : "待校验"}</p>
        <p>最大底层空隙：{solution.metrics.floor_largest_gap_mm ?? "—"} → {currentReview?.metrics?.floor_largest_gap_mm ?? "待校验"} mm</p>
      </aside>
    </div>
  </div>;
}
