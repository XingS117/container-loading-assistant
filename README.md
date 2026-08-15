# 装柜方案助手

面向外贸装柜场景的轻量计算网站。选择单个柜型，录入散箱或整托货物后，一次生成“装载率优先、重心稳妥、易操作、底层优先”四个可比较方案，并提供 3D、俯视、侧视、柜门和分层布局图。

**在线试用：** [https://packing.xingshuwen.com](https://packing.xingshuwen.com)

## 计算口径

- 只计算规则长方体货物；整托作为一个整体，不计算托盘内部码放。
- 所有布局都经过柜内边界、柜门、朝向、重量、碰撞、完整支撑、层数、顶部承重、易碎和必装校验。
- 当前候选生成器采用保守的完整支撑货物栈模型，不生成悬空或跨箱搭接。
- "装载率优先"在满足柜内边界、柜门、支撑、承重和必装等物理约束的前提下，优先装入件数和体积；它是在限定计算时间内找到的高质量可行解，不承诺数学上的全局最优。
- "重心稳妥"在保持"装载率优先"同一批货物的前提下做配重：纯整托订单按重量从柜中心向外网格配平，混装/散箱订单做同底面互换优化，并分别展示前后与左右偏差。
- "易操作"生成区域化布局（整带或整排），每个 SKU 尽量集中在连续编号区域，图与打印稿按区域编号并标注 SKU 与件数；订单过密放不下时允许少装并明确披露，必装货物不删。
- "底层优先"以铺满柜底为第一目标，剩余可叠货物只叠放在同规格货物的支撑位置上，并从柜长中部向两侧集中。
- 结果页在前后重量偏差较大时显示偏柜警告，并自动推荐更配平的方案。
- 标准柜参数是常用参考值，实际使用前应按承运柜的铭牌或箱单核对内尺寸、柜门尺寸和载重。

## 本地开发

要求 Node.js 20+ 和 Python 3.12+。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
pnpm install

# 终端一：计算 API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload

# 终端二：前端
pnpm dev
```

打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
pnpm test -- --run
pnpm build
```

## 单容器部署

```powershell
docker build -t container-loading-assistant .
docker run --rm -p 8000:8000 container-loading-assistant
```

打开 `http://127.0.0.1:8000`。生产环境应在容器前配置 HTTPS 反向代理，并保留 `/health` 健康检查。

## Excel 模板

在货物清单右上角点击“模板”下载固定格式，在 Excel 中填写后点击“导入 Excel”。原始文件只在浏览器内解析，不会上传；计算服务只接收结构化货物参数，也不会保存订单。

## 访问统计（可选）

项目支持通过 Umami 统计页面访问和“生成方案”次数，不会发送 SKU、尺寸、重量、订单内容或客户信息。复制 `frontend/.env.example` 为构建环境变量，填写 Umami 创建的网站 ID：

```powershell
$env:VITE_UMAMI_SCRIPT_URL = "https://cloud.umami.is/script.js"
$env:VITE_UMAMI_WEBSITE_ID = "你的 Umami website ID"
pnpm build
```

Docker 部署时，需要在构建阶段传入同名变量；仅在运行容器时设置变量不会改变已经构建好的前端。未配置这两个变量时，统计功能不会加载任何第三方脚本。

```powershell
docker build `
  --build-arg VITE_UMAMI_SCRIPT_URL="https://cloud.umami.is/script.js" `
  --build-arg VITE_UMAMI_WEBSITE_ID="你的 Umami website ID" `
  -t container-loading-assistant .
```
