import type { CargoInput, ContainerSpec } from "../types";

export interface SavedOrder {
  id: string;
  name: string;
  savedAt: string;
  container: ContainerSpec;
  cargoItems: CargoInput[];
  itemGapCm: number;
  clearanceCm: number;
}

const KEY = "container-loading-assistant-orders-v1";

export function loadSavedOrders(): SavedOrder[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]") as SavedOrder[]; } catch { return []; }
}

export function saveOrder(order: Omit<SavedOrder, "id" | "savedAt">): SavedOrder {
  const saved = { ...order, id: `order_${Date.now()}`, savedAt: new Date().toISOString() };
  const orders = [saved, ...loadSavedOrders().filter((item) => item.name !== order.name)].slice(0, 10);
  localStorage.setItem(KEY, JSON.stringify(orders));
  return saved;
}
