import type { CargoInput, ContainerSpec, PackResponse, PackingSolution, SolutionProfile } from "../types";

export interface SavedWorkspace {
  selectedProfile: SolutionProfile;
  solutionOverrides: Partial<Record<SolutionProfile, PackingSolution>>;
  lockedCargoIds: string[];
}

export interface SavedOrder {
  id: string;
  name: string;
  savedAt: string;
  container: ContainerSpec;
  cargoItems: CargoInput[];
  itemGapCm: number;
  clearanceCm: number;
  preferredProfile?: SolutionProfile;
  response?: PackResponse;
  workspace?: SavedWorkspace;
  source?: 'input' | 'calculation' | 'adjustment' | 'restore' | 'manual';
}

const KEY = "container-loading-assistant-orders-v1";

const profiles = ['high_fill', 'stable', 'easy'];
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(v => typeof v === 'string');
const numbers = (value: unknown) => record(value) && Object.values(value).every(v => typeof v === 'number' && Number.isFinite(v));
const finiteFields = (value: Record<string, unknown>, fields: string[]) => fields.every(key => typeof value[key] === 'number' && Number.isFinite(value[key]));

function validSolution(value: unknown): value is PackingSolution {
  if (!record(value) || !profiles.includes(value.profile as string) || typeof value.name !== 'string'
    || !numbers(value.loaded_counts) || !numbers(value.unloaded_counts) || !strings(value.pros) || !strings(value.cons) || !strings(value.warnings)
    || !record(value.metrics) || !numbers(value.metrics.center_of_gravity)
    || !finiteFields(value.metrics, ['loaded_pieces','loaded_weight_g','volume_utilization_pct','weight_utilization_pct','length_imbalance_pct','width_imbalance_pct','weight_imbalance_pct','loading_steps','cargo_zones'])) return false;
  return Array.isArray(value.placements) && value.placements.length <= 5000 && value.placements.every(p => record(p)
    && typeof p.id === 'string' && typeof p.cargo_id === 'string' && ['LWH','WLH','LHW','WHL','HLW','HWL'].includes(p.rotation as string)
    && finiteFields(p, ['instance_index','x_mm','y_mm','z_mm','length_mm','width_mm','height_mm','weight_g','step']))
    && Array.isArray(value.zones) && value.zones.every(z => record(z) && typeof z.cargo_id === 'string' && finiteFields(z,['step','x_mm','y_mm','length_mm','width_mm','piece_count']));
}

function validOrder(value: unknown): value is SavedOrder {
  if (!record(value) || typeof value.id !== 'string' || typeof value.name !== 'string' || typeof value.savedAt !== 'string'
    || !Number.isFinite(Date.parse(value.savedAt)) || !finiteFields(value,['itemGapCm','clearanceCm']) || !record(value.container)
    || typeof value.container.id !== 'string' || typeof value.container.name !== 'string'
    || !finiteFields(value.container,['inner_length_mm','inner_width_mm','inner_height_mm','door_width_mm','door_height_mm','max_payload_g'])
    || !Array.isArray(value.cargoItems) || value.cargoItems.length > 30 || !value.cargoItems.every(c => record(c) && typeof c.id === 'string' && typeof c.sku === 'string' && typeof c.name === 'string'
      && ['carton','pallet'].includes(c.kind as string) && ['upright','side','any'].includes(c.orientation_mode as string)
      && finiteFields(c,['length_cm','width_cm','height_cm','quantity','max_layers','max_top_load_kg']) && (c.weight_kg === null || typeof c.weight_kg === 'number' && Number.isFinite(c.weight_kg)))) return false;
  if (value.response !== undefined && (!record(value.response) || typeof value.response.request_id !== 'string'
    || !Array.isArray(value.response.solutions) || value.response.solutions.length !== 3 || !value.response.solutions.every(validSolution)
    || new Set(value.response.solutions.map(s => s.profile)).size !== 3)) return false;
  if (value.workspace !== undefined && (!value.response || !record(value.workspace) || !profiles.includes(value.workspace.selectedProfile as string)
    || !strings(value.workspace.lockedCargoIds) || !record(value.workspace.solutionOverrides)
    || !Object.entries(value.workspace.solutionOverrides).every(([profile, solution]) => validSolution(solution) && solution.profile === profile))) return false;
  return true;
}

function readStore(): unknown[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    if (!Array.isArray(value)) throw new Error();
    return value;
  } catch { throw new Error('本机历史无法读取或已损坏，本次未保存，原记录未覆盖。'); }
}

function writeStore(orders: unknown[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(orders)); }
  catch { throw new Error('本机空间不足或存储不可用，本次未保存。请删除不需要的历史后重试，当前页面方案仍保留。'); }
}

export function loadSavedOrders(): SavedOrder[] {
  try { return readStore().filter(validOrder); } catch { return []; }
}

export function saveOrder(order: Omit<SavedOrder, "id" | "savedAt">): SavedOrder {
  const orders = readStore();
  if (orders.length >= 10) throw new Error('本机已保存 10 个版本，本次未保存。请先删除不需要的历史，再保存当前方案。');
  const saved: SavedOrder = JSON.parse(JSON.stringify({
    id: `order_${crypto.randomUUID()}`, savedAt: new Date().toISOString(), name: order.name,
    container: order.container, cargoItems: order.cargoItems, itemGapCm: order.itemGapCm, clearanceCm: order.clearanceCm,
    preferredProfile: order.preferredProfile, response: order.response, workspace: order.workspace, source: order.source,
  }));
  if (!validOrder(saved)) throw new Error('订单或方案数据不完整，本次未保存。');
  writeStore([saved, ...orders]);
  return saved;
}

export function updateSavedSelection(id: string, selectedProfile: SolutionProfile): void {
  const orders = readStore();
  const saved = orders.find(o => validOrder(o) && o.id === id) as SavedOrder | undefined;
  if (!saved?.workspace) throw new Error('当前版本已不存在，请重新保存方案。');
  writeStore(orders.map(o => o === saved ? {...saved, workspace:{...saved.workspace, selectedProfile}} : o));
}

export function deleteSavedOrder(id: string): void {
  writeStore(readStore().filter(o => !record(o) || o.id !== id));
}
