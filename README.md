# Fanfic Sandbox

一个基于 Agent Tool Calling 的中文互动叙事沙盒。

现在的主架构已经统一为一个具备工具调用能力的 `GM Agent`：

- 大模型直接理解玩家动作
- 遇到有风险的行为时主动调用工具结算
- 用工具返回的事实更新状态
- 最后输出连续、沉浸式的剧情文本

底层仍然保留严格的代码结算和 `MutationLog -> State Mutator` 约束，所以它不是纯 Prompt 聊天，也不是让模型随口编结果。

## 当前架构

### 1. World Weaver

`/api/world/generate` 会把同人设定编译成 `WorldConfig`。

重点包括：

- `fanfic_meta`
- `world_book.campaign_context`
- `glossary`
- `topology`
- `mechanics`

### 2. GM Agent

`/api/game/start` 和 `/api/game/action` 都由 `GM Agent` 驱动。

Agent 的职责：

- 读取当前世界锚点、地点、角色状态、最近可见文本
- 拆解复合动作
- 连续调用工具
- 基于工具结果输出最终剧情

### 3. Runtime Tools

当前核心工具：

- `roll_d20_check`
- `modify_game_state`
- `inventory_manager`

这些工具会产出客观事实，并在需要时生成 `MutationLog`。

### 4. State Mutator

所有状态变更都必须通过 `MutationLog` 和 `apply_mutations` 落地，避免模型直接乱改状态。

## 项目状态

目前已接通：

- 世界生成
- Agent 化开局
- Agent 化行动回合
- 复合动作多次检定
- HP / MP / 位置修改
- 临时物品增删
- 审计面板
- Next.js 前端联调

## 技术栈

- Frontend: Next.js 15, React 19, TypeScript, Zustand
- Backend: FastAPI, Pydantic v2
- LLM: OpenAI-compatible Chat Completions + tool calling
- Tests: Pytest

## 环境要求

- Python 3.11+
- Node.js 20+
- npm 10+

## 安装

### Python

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

### Frontend

```powershell
npm install
```

## 环境变量

先复制模板：

```powershell
Copy-Item .env.example .env
```

最少需要：

```env
LLM_API_KEY=your_key
LLM_BASE_URL=https://your-openai-compatible-gateway.example/v1
LLM_MODEL_NAME=provider/model-name
ENGINE_API_BASE_URL=http://127.0.0.1:8000
```

可选：

```env
LLM_REQUEST_TIMEOUT_SECONDS=60
LLM_JSON_SCHEMA_PREFERRED=true
```

## 启动

### 后端

```powershell
py -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000
```

### 前端

```powershell
npm run dev
```

启动后访问：

- Frontend: `http://127.0.0.1:3000`
- Health: `http://127.0.0.1:8000/health`

## 常用命令

### 跑测试

```powershell
py -m pytest -q
```

### 构建前端

```powershell
npm run build
```

## API

- `POST /api/world/generate`
- `POST /api/game/start`
- `POST /api/game/action`
- `GET /health`

前端默认通过 Next.js 自带路由代理到后端。

## 项目结构

```text
app/                         Next.js 页面与前端 API 路由
components/                  前端面板组件
lib/                         前端状态、类型、格式化工具
server/agent/gm.py           GM Agent 主循环
server/agent/runtime_tools.py Agent 可调用工具
server/api/app.py            FastAPI 主入口
server/initialization/       世界织布机
server/generators/           生成器
server/llm/                  OpenAI-compatible client
server/runtime/session_store.py 会话态与运行时附加信息
server/schemas/core.py       核心 Pydantic Schema
server/state/mutator.py      状态修改器
tests/                       后端测试
```

## 运行规则

- 大模型不能直接拍板状态结果，必须通过工具和状态修改器落地
- 复合动作允许拆成多个子动作分别检定
- 所有玩家可见输出必须受当前时代和 `campaign_context` 约束
- 所有关键状态变化都应该能在审计面板中看到

## 当前已知边界

- Agent 已接管主运行链路，但底层战斗、掉落、地图等纯代码模块仍在仓库中，可继续复用
- 审计数据已经能反映多次检定和状态修改，但部分检定目标与场景快照还可以继续细化
- 如果模型没有足够积极地调用工具，后端有一层强制补结算兜底逻辑

## 示例输入

- `我假装投降，然后突然用魔杖射击天花板上的吊灯砸他，接着给自己加个护盾。`
- `我翻过柜台躲开扑击，再顺手抄起地上的铁管砸它膝盖。`
- `我先检查训练场地面上的刮痕，再顺着痕迹往树林方向追。`
- `我把刚捡到的护符塞进怀里，然后立刻往紧急出口跑。`
