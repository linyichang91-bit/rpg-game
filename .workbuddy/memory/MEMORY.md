# Fanfic Sandbox - 项目记忆

## 项目概览
- 同人 RPG 沙盒互动叙事系统
- 前端: Next.js 15 + React 19 + TypeScript + Zustand
- 后端: FastAPI + Pydantic v2 + Python 3.11+
- LLM: DeepSeek API (deepseek-chat)
- 前端代理: Next.js `/app/api/*` → 后端 `http://127.0.0.1:8000`

## 启动方式
- 后端: `.venv\Scripts\python.exe -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000`
- 前端: `npm run dev` (localhost:3000)
- 也可用: `powershell.exe -NoLogo -ExecutionPolicy Bypass -File .\scripts\Start-Dev.ps1`

## 环境配置
- .env 已配置 DeepSeek API
- Python venv 需在 `.venv` 目录
- node_modules 已安装

## 关键架构
- World Weaver: POST /api/world/generate
- GM Agent: POST /api/game/start, POST /api/game/action
- 存档: POST /api/game/save, /restore, /reset
- SessionStore: 内存态（重启后旧会话失效，存档可恢复）

## 战斗力 & 修为等级体系
- `PlayerState.power_level`: 抽象战斗力数值，由五维属性加权 + 等级加成 + 技能加成计算
- `PlayerState.rank_label`: 修为等级标签，从 `PowerScaling.power_tiers` 映射
- `PowerTier`: `{min_power, label}` 定义修为阶梯（如 0=下忍, 50=中忍）
- `server/runtime/power_level.py`: `compute_power_level()` 和 `resolve_rank_label()`
- 世界观生成时 LLM 自动生成 `power_tiers`，使用世界原生术语
- 前端隐藏五维数值，只展示战斗力数值 + 修为等级 + 定性描述（极强/强/良/平/弱/极弱）
- 每次 `_apply_logs` 后自动重算 power_level 和 rank_label

## GM 叙事质量问题（已修复 2026-04-12）
- **问题**：玩家输入时间跳跃/蒙太奇动作（如"修炼到18岁"）时，GM 回复变成硬编码模板短句
- **根因**：DeepSeek 在 tool loop 中反复被长度检查打回，耗尽6轮上限，触发 `_build_turn_fallback` 硬编码模板
- **修复**：
  1. 系统提示词新增规则23-26：时间跳跃不是风险动作，不需 roll_d20_check；只需一次 trigger_growth + 直接写蒙太奇叙述
  2. fallback 从硬编码模板改为 LLM 生成（`_generate_fallback_narration`），仅在 LLM 也失败时才用模板
  3. 长度检查新增 `_looks_like_time_skip()`，时间跳跃场景最低字数从500降到300
