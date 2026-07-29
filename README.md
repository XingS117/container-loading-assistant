# 装柜方案助手

面向外贸装柜场景的轻量计算网站。选择单个柜型，录入散箱或整托货物后，一次生成“装得多、更加稳妥、方便操作”三个可比较方案，并提供 3D、俯视、侧视、柜门和分层布局图。

## 计算口径

- 只计算规则长方体货物；整托作为一个整体，不计算托盘内部码放。
- 所有布局都经过柜内边界、柜门、朝向、重量、碰撞、完整支撑、层数、顶部承重、易碎和必装校验。
- 当前候选生成器采用保守的完整支撑货物栈模型，不生成悬空或跨箱搭接。
- “装得多”是在限定计算时间内找到的高质量可行解，不承诺数学上的全局最优。
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
