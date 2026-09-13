import type { CargoInput, ContainerSpec, Orientation, Placement } from "../types";

const nextRotation: Record<Orientation, Orientation> = { LWH: "WLH", WLH: "LWH", LHW: "WHL", WHL: "LHW", HLW: "HWL", HWL: "HLW" };

function intersects(left: Placement, right: Placement): boolean {
  return left.x_mm < right.x_mm + right.length_mm && left.x_mm + left.length_mm > right.x_mm
    && left.y_mm < right.y_mm + right.width_mm && left.y_mm + left.width_mm > right.y_mm
    && left.z_mm < right.z_mm + right.height_mm && left.z_mm + left.height_mm > right.z_mm;
}

export function rotateCargoPlacements(placements: Placement[], cargo: CargoInput, container: ContainerSpec, lockedCargoIds?: Set<string>): { placements: Placement[]; error?: string } {
  if (lockedCargoIds?.has(cargo.id)) return { placements, error: `${cargo.sku} 已锁定，请先解锁后再编辑` };
  const rotated = placements.map((placement) => {
    if (placement.cargo_id !== cargo.id) return placement;
    const rotation = nextRotation[placement.rotation];
    if (!cargo.orientation_mode || (cargo.orientation_mode === "upright" && !["LWH", "WLH"].includes(rotation))) return placement;
    return { ...placement, rotation, x_mm: placement.x_mm + (placement.length_mm - placement.width_mm) / 2, y_mm: placement.y_mm + (placement.width_mm - placement.length_mm) / 2, length_mm: placement.width_mm, width_mm: placement.length_mm };
  });
  if (rotated.some((placement, index) => placement.cargo_id === cargo.id && placement === placements[index] && cargo.orientation_mode !== "any")) {
    return { placements, error: `${cargo.sku} 当前不允许继续旋转` };
  }
  if (rotated.some((placement) => placement.x_mm + placement.length_mm > container.inner_length_mm || placement.y_mm + placement.width_mm > container.inner_width_mm || placement.z_mm + placement.height_mm > container.inner_height_mm)) {
    return { placements, error: `${cargo.sku} 旋转后超出柜体边界` };
  }
  for (let index = 0; index < rotated.length; index += 1) {
    for (let other = index + 1; other < rotated.length; other += 1) {
      if (intersects(rotated[index], rotated[other])) return { placements, error: `${cargo.sku} 旋转后与其他货物发生碰撞` };
    }
  }
  return { placements: rotated };
}

export function swapCargoPlacements(placements: Placement[], firstCargoId: string, secondCargoId: string, lockedCargoIds?: Set<string>): { placements: Placement[]; error?: string } {
  if (firstCargoId === secondCargoId) return { placements, error: "请选择两种不同货物" };
  if (lockedCargoIds?.has(firstCargoId) || lockedCargoIds?.has(secondCargoId)) return { placements, error: "锁定货物不能交换位置，请先解锁" };
  const first = placements.filter((item) => item.cargo_id === firstCargoId);
  const second = placements.filter((item) => item.cargo_id === secondCargoId);
  if (first.length !== second.length || first.some((item, index) => item.z_mm !== second[index]?.z_mm)) return { placements, error: "只有数量和层高匹配的货物可以交换" };
  const swapped = placements.map((item) => {
    if (item.cargo_id === firstCargoId) { const target = second[item.instance_index]; return target ? { ...item, x_mm: target.x_mm, y_mm: target.y_mm, z_mm: target.z_mm, step: target.step } : item; }
    if (item.cargo_id === secondCargoId) { const target = first[item.instance_index]; return target ? { ...item, x_mm: target.x_mm, y_mm: target.y_mm, z_mm: target.z_mm, step: target.step } : item; }
    return item;
  });
  return { placements: swapped };
}

export function movePlacement(placements: Placement[], placementId: string, x_mm: number, y_mm: number, container: ContainerSpec, lockedCargoIds?: Set<string>): { placements: Placement[]; error?: string } {
  const current = placements.find((item) => item.id === placementId);
  if (!current) return { placements, error: "找不到要调整的货物" };
  if (lockedCargoIds?.has(current.cargo_id)) return { placements, error: "锁定货物不能移动，请先解锁" };
  const moved = placements.map((item) => item.id === placementId ? { ...item, x_mm, y_mm } : item);
  if (moved.some((item) => item.x_mm < 0 || item.y_mm < 0 || item.x_mm + item.length_mm > container.inner_length_mm || item.y_mm + item.width_mm > container.inner_width_mm || item.z_mm + item.height_mm > container.inner_height_mm)) return { placements, error: "调整后超出柜体边界" };
  for (let index = 0; index < moved.length; index += 1) for (let other = index + 1; other < moved.length; other += 1) if (intersects(moved[index], moved[other])) return { placements, error: "调整后与其他货物发生碰撞" };
  return { placements: moved };
}
