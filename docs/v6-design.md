# Vidance v6 设计文档 — prompt 多轮核实

> 用户编号：**v7**。仓库版本号 v6（紧接 v5 之后）。
> 状态：🎯 待开发。目的：prompt 信息不全时打回，多轮对话核实用户详细需求。

---

## 1. 概述

### 1.1 问题

- 现有 auto/fast 链路：给一条概念就硬做，信息不全（无角色/无情绪/无时长/无风格取向）时 LLM 自己脑补，成片常与用户预期不符
- "不是很清楚就硬做" 是当前最大体验槽点

### 1.2 v6 目标

- LLM 对 prompt 做**完整度评估**（6 维：主体/场景/情绪/风格/时长/镜头感）
- 不完整 → **打回多轮对话核实**（≤3 轮防死循环），补齐缺口
- 对话状态机 + 会话管理（CLI + API），供 v7 前端嵌入对话 UI
- **不做自动补全**（用户明确倾向：宁可多问，不替用户编）

### 1.3 v6 范围

- `llm.assess_completeness(prompt)` → 6 维布尔 + 缺失项描述 + 追问建议
- 对话状态机：OPEN → COLLECTING → COMPLETE → LOCKED（≤3 轮，锁后即使缺项也放行并标注"按默认"）
- CLI：`ask` 交互式子命令 / `--interactive` 配合 auto
- API：`POST /api/dialog/start` + `GET /api/dialog/{id}` + `POST /api/dialog/{id}/reply`（供 v7 前端）
- 会话持久化 SQLite（复用 scheduler 的 tasks.sqlite 或独立 dialog 表）

### 1.4 核心验证点

- 空概念 / 缺角色 / 缺情绪 → 触发追问，回答后生成脚本命中补齐字段
- ≤3 轮锁死，不无限对话
- `auto --interactive` 端到端：多轮补全 → 出片

---

## 2. 架构设计

### 2.1 完整度评估（llm 扩展）

```python
def assess_completeness(prompt: str) -> dict:
    # 输出 {dimensions: {subject:0/1, scene:.., emotion:.., style:.., duration:.., shot:..},
    #       missing: ["角色描述", "情绪基调"], questions: ["主角是谁？什么形象？", ...]}
    # chat_json；dimensions 全 1 → complete
```

6 维定义：

| 维 | 含义 | 缺失常见 |
|----|------|---------|
| subject | 主体/主角明确 | 无角色、多义（"一艘船"谁开） |
| scene | 场景/环境 | 只给动作不给背景 |
| emotion | 情绪基调 | 悲/喜/紧张/平静未提 |
| style | 视觉风格 | 写实/动漫/电影感/色调 |
| duration | 目标时长 | 未指定 → 可默认 15-30s |
| shot | 镜头感/运镜 | 特写/全景/跟拍/一镜到底 |

### 2.2 对话状态机

```
prompt → assess
   ├─ complete → 直接进入编剧流水线（LOCKED）
   └─ incomplete → OPEN
        └─ reply → assess 更新（保留已确认字段做记忆）
             ├─ 补齐到 complete → LOCKED → 流水线
             └─ 轮次 ≥3 → LOCKED（强制放行，缺项标"按默认"）
```

会话存 `{dialog_id, turns[], dimensions, status}` 于 SQLite。

### 2.3 混合注入

- 补齐后的字段合并进概念 → 传给 v4 M1 scout / 编剧 `llm.script()`
- duration 缺省用 config `default_duration`（15-30s）
- style 缺省用 config `default_style`，emotion 缺省 calm 中性

### 2.4 不自动补全约定

- 评估器只报"缺失 + 怎么问"，不替用户填默认值塞进脚本（时长/风格等可点用 config 默认，但内容类缺项必须用户回答或明确说"随便"）

---

## 3. 核心流程

```
[1] dialog/start {concept} → assess → 返回 {status, missing, questions}
[2] 用户 reply → assess 增量更新（保留记忆）
[3] 循环至 complete 或 ≥3 轮 → LOCKED
[4] 合成增强概念 → 进 auto/scout 编剧流水线
[5] 出片
```

---

## 4. 接口规格

### 4.1 CLI

```
# 交互式（启动对话，句首回答）
python core/vidance.py ask "深海探险" --voice edge-xiaoxiao
# 或与 auto 组合（--interactive 先核实再走漏掉流程）
python core/vidance.py auto "深海探险" --interactive
# 非交互：direct 子命令跳过核实直接做（保持向后兼容）
python core/vidance.py auto "深海探险"          # 原行为，不核实
```

### 4.2 API（FastAPI :8894 扩展）

```
POST /api/dialog/start      {"concept": "..."}         → {dialog_id, status, missing, questions}
GET  /api/dialog/{id}                                  → {turns, status, dimensions}
POST /api/dialog/{id}/reply {"text": "一只发光的蓝鲸"}  → {status, missing?, questions?, concept}
```

供 v7 前端做对话 UI（输入概念 → 逐条追问 → 补全 → 一键提交任务）。

### 4.3 任务选项

`options.concept` 在核实后更新为增强概念；`options.dialog_id` 记录溯源。

---

## 5. 验收标准

1. 6 维评估对空概念/缺角色/缺情绪正确判缺并给出可回答问题
2. 多轮对话记忆正确（后轮不推翻已确认字段）
3. ≤3 轮强制锁死，不再追问
4. `auto --interactive` 端到端出片，脚本字段与确认内容一致
5. API 三端点可用，供 v7 前端调用

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 死循环追问 | 轮次上限 3 + LOCKED 强制放行 |
| LLM 评估不稳定 | chat_json 兜底全 missing=[]（当 complete） |
| 用户不想多问 | `--interactive` 可选，默认原直做行为不变 |
| 记忆漂移 | 每轮把已确认字段写回 prompt 上下文 |

---

## 7. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | assess_completeness() 6 维评估 | 评估器 |
| M2 | 对话状态机 + SQLite 持久化 | 状态机 |
| M3 | CLI ask / --interactive + 增强概念注入编剧 | CLI |
| M4 | API 三端点（供 v7 前端） | API |

> v7 前端依赖 M4 的 dialog API；v6 本体不阻塞 v7 其他页面。

---

## 8. 依赖与前置

- 复用：utils/llm.py（chat_json）、core/scheduler.py（SQLite 复用）
- 依赖 v4 M1 scout + 编剧（增强概念喂入）
- 无新外部服务
