import { useEffect, useState } from 'react';
import type { CalculationPhase } from '../lib/api';

export function CalculationProgress({ phase, startedAt }: { phase: CalculationPhase; startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return <aside className="calculation-progress no-print" aria-label="计算状态">
    <strong role="status">{{submitting:'正在提交订单', waiting:'等待计算结果', reading:'正在读取方案'}[phase]}</strong>
    <span>已等待 {seconds} 秒</span>
    <p>{seconds >= 30 ? '计算仍在等待响应，请勿重复提交。' : '计算期间已暂时锁定编辑，确保方案与订单一致。'}计时仅表示等待时间，不代表算法完成进度。</p>
  </aside>;
}
