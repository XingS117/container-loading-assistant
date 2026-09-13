import type { CargoInput, ContainerSpec, Orientation, Placement } from "../types";

const nextRotation: Record<Orientation, Orientation> = { LWH: "WLH", WLH: "LWH", LHW: "WHL", WHL: "LHW", HLW: "HWL", HWL: "HLW" };

function intersects(left: Placement, right: Placement): boolean {
  return left.x_mm < right.x_mm + right.length_mm && left.x_mm + left.length_mm > right.x_mm
    && left.y_mm < right.y_mm + right.width_mm && left.y_mm + left.width_mm > right.y_mm
    && left.z_mm < right.z_mm + right.height_mm && left.z_mm + left.height_mm > right.z_mm;
}

export function rotateCargoPlacements(placements: Placement[], cargo: CargoInput, container: ContainerSpec): { placements: Placement[]; error?: string } {
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
