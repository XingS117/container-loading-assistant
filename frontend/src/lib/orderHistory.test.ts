import { loadSavedOrders, saveOrder, deleteSavedOrder, updateSavedSelection } from './orderHistory';
import { createCargo } from './cargo';
import type { PackResponse } from '../types';

const key = 'container-loading-assistant-orders-v1';
const order = {name:'测试订单',container:{id:'c',name:'柜',inner_length_mm:3000,inner_width_mm:2000,inner_height_mm:2000,door_width_mm:2000,door_height_mm:2000,max_payload_g:1000000,clearance_mm:0},cargoItems:[createCargo('A')],itemGapCm:0,clearanceCm:0};
const response: PackResponse = {request_id:'history',solutions:['high_fill','stable','easy'].map(profile => ({profile:profile as 'high_fill',name:profile,placements:[],loaded_counts:{[order.cargoItems[0].id]:0},unloaded_counts:{[order.cargoItems[0].id]:10},metrics:{loaded_pieces:0,loaded_weight_g:0,volume_utilization_pct:0,weight_utilization_pct:0,center_of_gravity:{x_mm:0,y_mm:0,z_mm:0},length_imbalance_pct:0,width_imbalance_pct:0,weight_imbalance_pct:0,loading_steps:0,cargo_zones:0},zones:[],pros:[],cons:[],warnings:[],identical_to:null}))};
beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

test('keeps same-name versions independently and reads legacy input-only orders', () => {
  const first = saveOrder(order); const second = saveOrder(order);
  expect(first.id).not.toBe(second.id);
  expect(loadSavedOrders()).toHaveLength(2);
  localStorage.setItem(key,JSON.stringify([{...order,id:'legacy',savedAt:'2026-09-13T00:00:00Z'}]));
  expect(loadSavedOrders()[0].id).toBe('legacy');
});

test('stores complete independent snapshots including overrides, locks, remaining cargo and selection', () => {
  const workspace = {selectedProfile:'easy' as const,lockedCargoIds:[order.cargoItems[0].id],solutionOverrides:{easy:{...response.solutions[2],warnings:['已调整']}}};
  const saved = saveOrder({...order,response,workspace,preferredProfile:'stable',source:'adjustment'});
  workspace.lockedCargoIds.push('later');
  expect(loadSavedOrders()[0]).toMatchObject({response,preferredProfile:'stable',workspace:{selectedProfile:'easy',lockedCargoIds:[order.cargoItems[0].id],solutionOverrides:{easy:{warnings:['已调整']}}}});
  updateSavedSelection(saved.id,'stable');
  expect(loadSavedOrders()).toHaveLength(1);
  expect(loadSavedOrders()[0].workspace?.selectedProfile).toBe('stable');
  deleteSavedOrder(saved.id);
  expect(loadSavedOrders()).toEqual([]);
});

test('quota failures and full history do not overwrite earlier versions', () => {
  saveOrder(order); const before = localStorage.getItem(key);
  const write = vi.spyOn(Storage.prototype,'setItem').mockImplementation(() => {throw new DOMException('full','QuotaExceededError');});
  expect(() => saveOrder(order)).toThrow(/未保存/);
  expect(localStorage.getItem(key)).toBe(before);
  write.mockRestore();
  for (let i=1;i<10;i++) saveOrder(order);
  expect(() => saveOrder(order)).toThrow(/10/);
  expect(loadSavedOrders()).toHaveLength(10);
});

test('ignores corrupt records for display but refuses to overwrite an unreadable store', () => {
  localStorage.setItem(key,JSON.stringify([null,{}, {...order,id:'good',savedAt:'2026-09-13T00:00:00Z'}, {...order,id:'broken',savedAt:'date',response:{solutions:[{}]}}]));
  expect(loadSavedOrders().map(o => o.id)).toEqual(['good']);
  localStorage.setItem(key,'{broken');
  expect(loadSavedOrders()).toEqual([]);
  expect(() => saveOrder(order)).toThrow(/损坏/);
  expect(localStorage.getItem(key)).toBe('{broken');
});

test('does not persist unrelated model configuration or credentials alongside the order', () => {
  saveOrder({...order,apiKey:'private-test-key',aiConfig:{apiKey:'private-test-key'}} as typeof order);
  expect(localStorage.getItem(key)).not.toContain('private-test-key');
});

test('rejects malformed optional diagnostics instead of crashing history rendering', () => {
  const saved=saveOrder({...order,response});
  const broken=JSON.parse(JSON.stringify(saved));
  broken.response.solutions[0].assessment={status:'heuristic',diagnostics:{}};
  localStorage.setItem(key,JSON.stringify([broken,saved]));
  expect(loadSavedOrders()).toHaveLength(1);
  expect(localStorage.getItem(key)).toContain('diagnostics');
});
