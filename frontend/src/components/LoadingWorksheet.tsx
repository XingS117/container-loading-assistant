import { buildLoadingWorksheet } from '../lib/loadingWorksheet';
import type { CargoInput, PackingSolution } from '../types';

interface Props {
  solution: PackingSolution;
  cargoItems: CargoInput[];
  clearanceMm: number;
  print?: boolean;
}

export function LoadingWorksheet({ solution, cargoItems, clearanceMm, print = false }: Props) {
  const rows = buildLoadingWorksheet(solution.placements, cargoItems, clearanceMm);
  if (!rows.length) return <p className="worksheet-empty">当前方案没有已装货物，暂无作业单。</p>;
  const issueCount = rows.filter(row => row.issues.length > 0).length;
  return <section className={`loading-worksheet${print ? ' loading-worksheet--print' : ''}`} aria-label={`${solution.name}仓库作业单`}>
    <div className="worksheet-guide">
      <p><strong>按步骤装载，同一步内按表格从上往下执行。</strong>本表仅对应「{solution.name}」，共 {rows.length} 件；三种方案是备选方案，请选定一份执行。</p>
      <p>位置 X / Y / Z（cm）指货物靠近原点的底角：X 从柜头向柜门，Y 对应俯视图上边至下边，Z 从柜底向上。摆放尺寸沿柜长 × 柜宽 × 柜高，原始货物尺寸不变。</p>
      <p>固定检查：装载前确认防滑与填隙材料，装载后逐区确认防移位、绑扎和柜门净空；具体固定方式由现场根据包装与运输条件确定。</p>
      <small>重件提醒阈值为单件 25 kg，仅提示确认搬运方式，不是安全或法规标准。</small>
    </div>
    {issueCount > 0 && <p className="worksheet-issue" role="alert">有 {issueCount} 件需要重新复核，请先处理表内问题，再用于现场装载。</p>}
    <div className="worksheet-scroll" tabIndex={print ? undefined : 0} role={print ? undefined : 'region'} aria-label={print ? undefined : '逐件作业表，可横向和纵向滚动'}>
      <table className="worksheet-table" aria-label={`${solution.name}逐件作业表`}>
        <colgroup><col style={{ width: '8%' }} /><col style={{ width: '15%' }} /><col style={{ width: '14%' }} /><col style={{ width: '22%' }} /><col style={{ width: '16%' }} /><col style={{ width: '20%' }} /><col style={{ width: '5%' }} /></colgroup>
        <thead>
          <tr><th colSpan={7}>{solution.name} · 逐件作业单 · 共 {rows.length} 件</th></tr>
          <tr><th scope="col">步骤</th><th scope="col">货物 / 单重</th><th scope="col">位置 X / Y / Z<br />cm</th><th scope="col">摆放尺寸 / 朝向</th><th scope="col">下方承载</th><th scope="col">搬运与固定提示</th><th scope="col">核对</th></tr>
        </thead>
        <tbody>{rows.map(row => <tr key={row.placement.id}>
          <td>第 {row.placement.step} 步</td>
          <td><strong>{row.label}</strong><small>{row.weightKg} kg</small></td>
          <td>{row.positionCm}</td>
          <td><strong>{row.sizeCm}</strong><small>cm · {row.placement.rotation}</small><span>{row.orientation}</span></td>
          <td>{row.supportLabels.length ? row.supportLabels.map(label => <span key={label}>{label}</span>) : row.placement.z_mm <= clearanceMm ? '柜底' : '未找到支撑件'}</td>
          <td>{row.notes.length ? row.notes.map(note => <span key={note}>{note}</span>) : <span>确认防滑、填隙与固定</span>}{row.issues.map(issue => <strong className="worksheet-issue" key={issue}>{issue}</strong>)}</td>
          <td><span className="worksheet-check" aria-label="纸面核对栏" /></td>
        </tr>)}</tbody>
      </table>
    </div>
    {print && <div className="worksheet-signature" aria-label="纸面复核签名">
      <p>现场核对：□ 件数与货物　□ 位置与朝向　□ 支撑与承重　□ 固定措施　□ 柜门净空</p>
      <div><span>装载人：________________</span><span>复核人：________________</span><span>日期：________________</span></div>
      <p>异常及处理记录：________________________________________________________________</p>
      <small>请完成现场复核后签字；空白栏不代表已确认。</small>
    </div>}
  </section>;
}
