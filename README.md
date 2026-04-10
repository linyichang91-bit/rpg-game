# Fanfic Sandbox

一个面向中文互动叙事的同人 RPG 沙盒。

它不是单纯的“聊天式跑团”，而是一套前后端分离的互动叙事系统：

- 前端负责世界生成入口、叙事面板、状态面板、审计面板和本地存档槽位
- 后端负责世界编织、会话状态、GM Agent、工具调用和状态结算
- 大模型负责理解玩家意图、驱动工具链并输出最终叙事

当前项目已经支持从“设定一句话”直接进入可游玩的开局场景，并支持后续自由输入、读档、重置和持续推进。

## 主要能力

- 世界生成：把用户输入的同人设定编译成结构化 `WorldConfig`
- 开场生成：生成可直接进入游玩的序章 / 第一幕
- Agent 回合驱动：通过 GM Agent 调用工具、解析动作、输出叙事
- 状态结算：HP / MP / 物品 / 任务 / 遭遇 / 位置变化通过后端状态层落地
- 审计面板：前端可查看本回合执行事件与状态变更
- 本地存档槽：支持手动存档、读档、删除存档、清空全部存档
- 会话恢复：读档后会在后端恢复成新的有效会话，而不是只恢复前端画面
- 会话重置：支持重置当前冒险，回到开局界面

## 当前架构

### 1. World Weaver

`POST /api/world/generate`

负责把用户的同人 prompt 转成后端可运行的世界配置，包括：

- `fanfic_meta`
- `world_book.campaign_context`
- `glossary`
- `topology`
- `mechanics`
- `initial_quests`

同时会生成长篇开场序章文本。

### 2. GM Agent

`POST /api/game/start`  
`POST /api/game/action`

GM Agent 会基于当前状态快照：

- 理解玩家输入
- 判断是否需要工具调用
- 通过运行时工具完成风险结算
- 提交状态变化
- 输出最终的玩家可见叙事

### 3. Runtime Tools

当前核心运行时工具包含：

- `roll_d20_check`
- `modify_game_state`
- `inventory_manager`
- `resolve_combat_action`
- `resolve_exploration_action`
- `resolve_loot_action`
- `update_quest_state`
- `update_encounter_state`

### 4. Session Store

会话状态由后端内存态 `SessionStore` 管理，前端本地还会额外持久化：

- 当前会话快照
- 叙事日志
- 审计日志
- 手动存档槽

读档时会调用后端恢复接口，重新生成一个新的 `session_id`，保证之后还能继续结算。

## 技术栈

- Frontend: Next.js 15, React 19, TypeScript, Zustand
- Backend: FastAPI, Pydantic v2
- LLM: OpenAI-compatible Chat Completions / Tool Calling
- Tests: Pytest

## 环境要求

- Python 3.11+
- Node.js 20+
- npm 10+

## 快速开始

### 1. 安装依赖

Python 依赖：

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

前端依赖：

```powershell
npm install
```

### 2. 配置环境变量

复制模板：

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

可选项：

```env
LLM_REQUEST_TIMEOUT_SECONDS=30
LLM_JSON_SCHEMA_PREFERRED=true
```

### 3. 启动项目

#### 方案 A：手动分别启动

后端：

```powershell
.venv\Scripts\python.exe -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
npm run dev
```

#### 方案 B：使用项目脚本启动

仓库里提供了 Windows PowerShell 脚本：

- `scripts/Set-ProjectEnv.ps1`
- `scripts/Start-Dev.ps1`

其中：

- `Set-ProjectEnv.ps1` 会注入 UTF-8 控制台编码和项目本地工具链路径
- `Start-Dev.ps1` 会同时拉起前后端，并把日志写到 `backend.log` / `frontend.log`

启动命令：

```powershell
powershell.exe -NoLogo -ExecutionPolicy Bypass -File .\scripts\Start-Dev.ps1
```

启动后访问：

- Frontend: `http://127.0.0.1:3000`
- Backend health: `http://127.0.0.1:8000/health`

## 常用命令

运行测试：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

仅跑关键 API / Agent 测试：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_api_app.py tests/test_gm_agent.py
```

前端生产构建：

```powershell
npm run build
```

## API 概览

### 世界与会话

- `GET /health`
- `POST /api/world/generate`
- `POST /api/game/start`
- `POST /api/game/action`

### 存档与恢复

- `POST /api/game/save`
- `POST /api/game/restore`
- `POST /api/game/reset`

前端默认通过 Next.js 的 `/app/api/*` 路由代理到后端 `ENGINE_API_BASE_URL`。

## 前端界面

当前前端主要由以下几个区域组成：

- 创世界面：输入同人设定并生成世界
- 叙事剧场：展示系统旁白与玩家输入
- 状态面板：展示属性、物品、任务、遭遇、位置
- 审计面板：查看本回合工具执行与状态变更
- 档案舱：手动存档、读档、删除、清空本地存档、重置当前会话

## 项目结构

```text
app/                              Next.js 页面与 API 代理路由
components/                       前端 UI 组件
lib/                              前端状态、类型、API 封装
scripts/                          Windows 启动与环境脚本
server/api/app.py                 FastAPI 入口
server/agent/gm.py                GM Agent 主流程
server/agent/runtime_tools.py     Agent 可调用的运行时工具
server/initialization/            World Weaver 与开场生成
server/runtime/session_store.py   会话状态与恢复逻辑
server/schemas/core.py            核心 Pydantic Schema
server/state/mutator.py           状态修改器
server/generators/                地图 / 掉落等生成器
tests/                            后端测试
```

## 设计约束

- 模型不能直接随意篡改状态，关键状态变更必须经过后端工具和状态层
- 叙事输出必须服从世界锚点、当前地点、当前会话状态
- 玩家复合动作允许拆分成多个子动作分别结算
- 读档恢复后必须继续能玩，而不是只恢复前端日志
- 所有关键回合变化应能在审计面板中追踪

## 当前已实现

- 世界生成
- 开场叙事
- 玩家自由输入回合推进
- 基础战斗 / 探索 / 搜刮工具链
- 任务与遭遇状态更新
- 审计追踪
- 手动存档 / 读档 / 删除 / 清空本地存档
- 当前会话重置

## 已知边界

- 当前后端 `SessionStore` 仍是内存态；服务端进程重启后，旧会话会失效，但手动存档仍可重新恢复
- LLM 网关质量会直接影响叙事质量、工具调用积极性和开场长度
- 目前没有数据库版永久存档，存档槽位保存在浏览器本地 `localStorage`
- 部分旧文件里仍存在历史编码问题，README 已重写为正常 UTF-8 中文

## 示例输入

- `咒术回战同人，主角在 2018 年 5 月 1 日入学东京咒术高专，拥有投影与强化能力，开场就在宿舍醒来。`
- `火影忍者 AU，主角是木叶下忍，在中忍考试前夜被卷入一场针对村子的阴谋。`
- `哈利波特现代 AU，主角是麻瓜出身的新生，被迫在开学列车上第一次接触魔法世界。`

## 后续方向

- 自动存档 / 快速存档
- 更完整的地图与探索分支
- 更丰富的掉落、装备和任务系统
- 持久化数据库存档
- 更稳定的中文编码与本地开发体验
