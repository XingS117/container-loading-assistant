import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';

const container={id:'40hq',name:'40HQ',inner_length_mm:12032,inner_width_mm:2352,inner_height_mm:2698,door_width_mm:2340,door_height_mm:2585,max_payload_g:28600000,clearance_mm:0};
beforeEach(()=>{localStorage.clear();sessionStorage.clear();vi.restoreAllMocks();});

test('loads a complete example with assumptions disclosed and no model setup',async()=>{
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify([container])));
  const confirm=vi.spyOn(window,'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<App/>);
  await screen.findByRole('button',{name:/40HQ/});
  const start=screen.getByRole('button',{name:'载入完整示例'});
  await userEvent.click(start);
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('SKU-001');
  await userEvent.click(start);
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('ZT1');
  expect(screen.getByLabelText('单重 ZT1')).toHaveValue(150);
  expect(screen.getByLabelText('单重 ZT2')).toHaveValue(280);
  expect(screen.getByLabelText('单重 ZT3')).toHaveValue(400);
  expect(screen.getByLabelText('本次优先目标')).toHaveValue('stable');
  expect(screen.getByText(/500 kg.*示例假设/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'生成装柜方案'})).toBeEnabled();
});
