import { act, render, screen } from '@testing-library/react';
import { CalculationProgress } from './CalculationProgress';

test('reports elapsed time without inventing progress and releases its timer', () => {
  vi.useFakeTimers();
  try {
    const {unmount} = render(<CalculationProgress phase="waiting" startedAt={Date.now()} />);
    expect(screen.getByText('已等待 0 秒')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(31000); });
    expect(screen.getByText('已等待 31 秒')).toBeInTheDocument();
    expect(screen.getByText(/不代表算法完成进度/)).toBeInTheDocument();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  } finally { vi.useRealTimers(); }
});
