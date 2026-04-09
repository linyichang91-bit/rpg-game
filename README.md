# 通用叙事世界引擎 / Fanfic Sandbox

一个“**大模型辅助决策 + 严格代码结算**”的中文互动叙事沙盒。

这个项目不是纯 Prompt 聊天机器人，而是一个分层引擎：

- `Central Brain` 只负责意图解析与路由
- `Pipelines` 只负责代码结算
- `Narrator` 只负责基于事实做中文叙事包装
- 所有状态修改都必须经过 `MutationLog -> State Mutator`

当前前端、后端、中文叙事、战斗、搜刮、动态建图都已经接通，可直接本地试玩。

## 当前已实现

- 世界初始化 / 同人世界织布机
- 核心 Schema 与 `State Mutator`
- `Central Brain` 路由中枢
- 战斗管线：D20 命中 + 敌人反击
- 搜刮管线：候选池生成 + 代码掷骰掉落
- 探索管线：未知地点即时建图 + `topology` 持久化扩张
- `Narrator` 中文叙事渲染
- Next.js 多面板控制台
- 审计面板：`ExecutedEvent` / `MutationLog` / `topology` 快照

## 技术栈

- 前端：Next.js 15、React 19、Zustand、TypeScript
- 后端：FastAPI、Pydantic v2、OpenAI-compatible API client
- 测试：Pytest

## 环境要求

- Python 3.11+
- Node.js 20+
- npm 10+

本仓库当前在 Windows + PowerShell 环境下验证通过。

## 安装依赖

### 1. Python 依赖

建议先创建虚拟环境：

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

如果你用 macOS / Linux，把 `py` 换成 `python3`，激活命令换成：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### 2. 前端依赖

```powershell
npm install
```

## 环境变量

项目根目录使用同一个 `.env`，前后端都会读取它。

先复制模板：

```powershell
Copy-Item .env.example .env
```

最少需要这些变量：

```env
LLM_API_KEY=your_key
LLM_BASE_URL=https://your-openai-compatible-gateway.example/v1
LLM_MODEL_NAME=provider/model-name
ENGINE_API_BASE_URL=http://127.0.0.1:8000
```

可选变量：

```env
LLM_REQUEST_TIMEOUT_SECONDS=30
LLM_JSON_SCHEMA_PREFERRED=true
```

说明：

- `LLM_API_KEY`：大模型网关密钥
- `LLM_BASE_URL`：兼容 OpenAI Chat Completions 的网关地址
- `LLM_MODEL_NAME`：模型名
- `ENGINE_API_BASE_URL`：Next.js 代理转发到后端 API 的地址

## 启动方式

### 方式 A：开发联调

先开后端：

```powershell
py -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000
```

再开前端：

```powershell
npm run dev
```

启动后访问：

- 前端：`http://127.0.0.1:3000`
- 后端健康检查：`http://127.0.0.1:8000/health`

### 方式 B：前端生产模式预览

```powershell
npm run build
npm run start
```

后端依然需要单独启动：

```powershell
py -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000
```

## 常用命令

### 跑后端测试

```powershell
py -m pytest -q
```

### 验证前端构建

```powershell
npm run build
```

## 项目结构

```text
app/                         Next.js 页面与前端 API 路由
components/                  前端面板组件
lib/                         前端状态、类型、格式化工具
server/api/app.py            FastAPI 主入口
server/brain/central.py      路由中枢 / 意图解析
server/schemas/core.py       核心 Pydantic Schema
server/state/mutator.py      统一状态修改器
server/initialization/       世界织布机
server/generators/           候选池生成器 / 动态地图生成器
server/pipelines/            战斗 / 搜刮 / 探索管线
server/narrative/narrator.py 叙事渲染引擎
server/runtime/session_store.py 会话态与运行时附加信息
tests/                       后端测试
```

## 核心运行规则

这几个约束是项目设计底线：

- 大模型不能直接决定战斗结果、掉落结果或任务推进
- 代码层只使用抽象 Key，不写死世界观名词
- `Narrator` 只能叙述 `ExecutedEvent`，不能发明事实
- 所有状态变化必须通过 `MutationLog` 和 `apply_mutations`

## 当前 API 入口

- `POST /api/world/generate`
- `POST /api/game/start`
- `POST /api/game/action`
- `GET /health`

前端默认通过 Next.js 自带的代理路由转发到后端：

- `/api/world/generate`
- `/api/game/start`
- `/api/game/action`

## 当前可直接试玩的输入示例

- `我拔出武器攻击敌人`
- `我仔细搜查倒下的敌人尸体`
- `查看我的状态`
- `去后山的神秘山洞`
- `去山洞深处的祭坛`
- `去祭坛下的暗道`

## 备注

- 玩家可见前端文案与叙事输出已切为简体中文
- 审计面板会展示事实日志与地图增长过程，方便观察“代码结算而不是模型胡编”
- 如果未来重新初始化 Git 仓库，优先从 `server/api/app.py`、`server/brain/central.py`、`server/pipelines/` 和本 README 继续接续开发
