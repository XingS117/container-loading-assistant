import type { CargoInput, Orientation, Placement } from '../types';

export interface LoadingWorksheetRow {
  placement: Placement;
  label: string;
  positionCm: string;
  sizeCm: string;
  orientation: string;
  weightKg: number;
  supportLabels: string[];
  notes: string[];
  issues: string[];
}

export function orientationLabel(rotation: Orientation): string {
  const names: Record<string, string> = { L: '原长', W: '原宽', H: '原高' };
  return `${names[rotation[0]]}沿柜长，${names[rotation[1]]}沿柜宽，${names[rotation[2]]}朝上`;
}

export function buildLoadingWorksheet(placements: Placement[], cargoItems: CargoInput[], clearanceMm: number): LoadingWorksheetRow[] {
  const cargoById = new Map(cargoItems.map(c => [c.id, c]));
  const ordered = [...placements].sort((a, b) => a.step - b.step || a.z_mm - b.z_mm || a.x_mm - b.x_mm || a.y_mm - b.y_mm || a.id.localeCompare(b.id));
  const order = new Map(ordered.map((p, i) => [p.id, i]));
  const topSurfaces = new Map<number, Placement[]>();
  for (const p of ordered) {
    const top = p.z_mm + p.height_mm;
    const group = topSurfaces.get(top) ?? [];
    group.push(p);
    topSurfaces.set(top, group);
  }
  const label = (p: Placement) => `${cargoById.get(p.cargo_id)?.sku ?? p.cargo_id} · 第 ${p.instance_index + 1} 件`;
  return ordered.map((p, index) => {
    const cargo = cargoById.get(p.cargo_id);
    const issues: string[] = [];
    const notes: string[] = [];
    const supporters: Placement[] = [];
    let supportedArea = 0;
    if (p.z_mm > clearanceMm) {
      for (const support of topSurfaces.get(p.z_mm) ?? []) {
        const overlapX = Math.max(0, Math.min(p.x_mm + p.length_mm, support.x_mm + support.length_mm) - Math.max(p.x_mm, support.x_mm));
        const overlapY = Math.max(0, Math.min(p.y_mm + p.width_mm, support.y_mm + support.width_mm) - Math.max(p.y_mm, support.y_mm));
        if (overlapX * overlapY > 0) { supporters.push(support); supportedArea += overlapX * overlapY; }
      }
      if (supportedArea < p.length_mm * p.width_mm) issues.push('支撑不完整，请重新复核布局');
      if (supporters.some(s => (order.get(s.id) ?? index) >= index)) issues.push('支撑件装载顺序在后，请重新复核步骤');
      notes.push('先完成下方支撑件，再放本件；复核防滑与固定');
    }
    if (!cargo) issues.push('货物资料缺失，请核对订单');
    if (p.weight_g >= 25000) notes.push('重件：确认搬运设备及人员安排');
    if (cargo?.kind === 'pallet') notes.push('整托：使用适配的托盘搬运设备');
    if (cargo?.fragile) notes.push('易碎：轻放、防冲击，复核包装保护');
    if (cargo?.kind === 'carton' && (!cargo.stackable || cargo.fragile)) notes.push('顶部禁压');
    if (cargo?.kind === 'pallet' && !cargo.stackable) notes.push('上方不可叠放整托');
    if (cargo?.unload_order) notes.push(`卸货批次 ${cargo.unload_order}`);
    return {
      placement: p, label: label(p),
      positionCm: [p.x_mm, p.y_mm, p.z_mm].map(v => v / 10).join(' / '),
      sizeCm: [p.length_mm, p.width_mm, p.height_mm].map(v => v / 10).join(' × '),
      orientation: orientationLabel(p.rotation), weightKg: p.weight_g / 1000,
      supportLabels: supporters.map(label), notes, issues,
    };
  });
}
