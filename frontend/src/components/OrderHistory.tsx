import type { SavedOrder } from '../lib/orderHistory';

interface Props {
  orders: SavedOrder[];
  name: string;
  onNameChange: (name: string) => void;
  onSave: () => void;
  onRestore: (order: SavedOrder, copy: boolean) => void;
  onDelete: (order: SavedOrder) => void;
  hasResult: boolean;
  message: string;
}

export function OrderHistory({orders, name, onNameChange, onSave, onRestore, onDelete, hasResult, message}: Props) {
  const sources = {input:'输入清单',calculation:'计算结果',adjustment:'人工调整',restore:'恢复原始布局',manual:'手动保存'};
  return <section className="order-history no-print" aria-label="本机订单与方案历史">
    <div className="order-history-save">
      <label>订单名称<input aria-label="订单名称" maxLength={80} value={name} onChange={e => onNameChange(e.target.value)} /></label>
      <button type="button" onClick={onSave}>{hasResult ? '保存当前方案版本' : '保存当前订单'}</button>
      {message && <p role="status">{message}</p>}
    </div>
    <details>
      <summary>本机历史 · {orders.length} / 10 个版本</summary>
      <p className="history-note">计算成功和应用调整后自动保存。仅保存在当前浏览器；清除站点数据会丢失。历史快照未按当前规则重新复核。</p>
      {orders.length ? <ul>{orders.map(order => {
        const profile = order.workspace?.selectedProfile;
        const selected = profile ? order.workspace?.solutionOverrides[profile] ?? order.response?.solutions.find(s => s.profile === profile) : order.response?.solutions[0];
        return <li key={order.id}>
          <div><strong>{order.name}</strong><small>{new Date(order.savedAt).toLocaleString('zh-CN')} · {sources[order.source ?? 'input']} · {order.container.name}</small>
            <small>{selected ? `${selected.name}：已装 ${selected.placements.length} 件 / 未装 ${Object.values(selected.unloaded_counts).reduce((sum, n) => sum + n, 0)} 件` : `${order.cargoItems.length} 种货物 · 仅输入清单`}</small></div>
          <div className="history-row-actions"><button type="button" onClick={() => onRestore(order, false)}>{order.response ? '查看此版本' : '恢复输入'}</button><button type="button" onClick={() => onRestore(order, true)}>复制为新订单</button><button type="button" onClick={() => onDelete(order)}>删除版本</button></div>
        </li>;
      })}</ul> : <p>尚无已保存记录。</p>}
    </details>
  </section>;
}
