# Vidance v7 设计文档 — 前端（agent WebUI）

> 仓库版本号 v7（紧接 v6 之后）。
> 状态：🚧 M1-M4 代码完成（2026-09-15）。后端新端点全部 curl 实测（tools/options/tool 分发 9 命令/video 流/ask 接 v6 dialog）；前端 `ui/index.html` 自包含（零 npm，21KB）+ JS 语法 node 校验通过；**全服务关停联测通过**：fast 任务降级跑完（TTS 缺省静音 + Pexels 照常）→ 6.0s 成片 completed → video 流 200，前端三类卡片数据形态齐验；浏览器渲染与真声 TTS 出片待 GPU 恢复验证。

---

## 1. 概述

### 1.1 现状 / 动机

- 目前全走 CLI（vidance.py / scheduler.py），运营上手门槛高
- 成片散落 output/，无集中画廊/播放
- 缺**可视化对话式**入口：用户想"对话式下指令、看进度、看成片"

### 1.2 v7 目标

- **仿豆包/智象未来布局**的对话式 webUI：
  - **左侧功能边栏**（会话列表、历史任务、工具入口）
  - **上方一大片空白**：画布 / 对话内容区（进出消息 + 视频卡片），中间一大片白，衬托对话
  - **右下方**：对话框（输入框 + 发送）
- 支持 **`/` 斜杠命令**触发既有 tool（/auto /fast /hot /ask /clip /scout /voices /video…），也可自然语言（左侧工具栏点按钮插入 `/` 命令）
- 自包含 HTML + vanilla JS（FastAPI 静态服务，**零 npm**，npm/GitHub 被墙）
- 后端复用既有 API + 少量新端点（含 tool 统一分发）

### 1.3 v7 范围

- 静态服务托管单页 webUI（左栏 + 顶部画布 + 右下发框）
- 新端点：
  - `GET /api/video/{task_id}`（视频流）
  - `GET /api/options`（音色/角色/LUT/BGM/特效/moods 枚举）
  - `GET /api/tools`（可用的 `/` 命令清单 + 参数 schema，供前端渲染自动补全）
  - `POST /api/tool/{name}`（统一 tool 分发：解析斜杠命令 → 调对应后端逻辑 → 返回结果/任务）
- 复用：/api/tasks、(api/tasks/{id})、/api/dashboard、/api/scout、/api/dialog/*（v6）、/api/clone_voice、/api/clip
- 移动端可用（响应式：窄屏左栏折叠）

### 1.4 核心验证点

- 打开 http://127.0.0.1:8894/ui 即用，无构建
- 左栏 + 顶部画布 + 右下发框整体呈现（仿豆包感）
- 输入 `/` 弹命令补全，选 /fast 填参 → 提交 → 顶部画布轮流询进度 → 出片可播
- 自然语言消息也能回复（简单意图 → 建议 / 命令）

---

## 2. 架构设计

### 2.1 技术选型

- **零依赖自包含**：单 HTML 内嵌 CSS+JS（或少量分离文件），原生 fetch + 轮询
- 后端：扩展现有 `core/api_server.py`（FastAPI :8894），`StaticFiles` 挂 /ui
- 与 `core/dashboard.py`（v4 HTML 仪表盘）风格统一

### 2.2 页面结构（仿豆包/智象未来）

```
┌───────────────────────────────────────────────┐
│  顶部：品牌 + 会话标题 + 新建按钮（右侧）              │
├──────────┬────────────────────────────────────┤
│  左侧功能  │  上方一大片空白（对话/画布区）               │
│  边栏     │  - 用户消息（右下气泡）+ 机器人回复          │
│  ├ 会话列表 │  - 进度卡片 / 成片 video 卡片            │
│  ├ 历史任务 │  - 中间大面积留白，衬托对话（豆包感）        │
│  ├ 工具入口 │                                      │
│  │ /auto  │  ──────────────────────────────    │
│  │ /fast  │  右下发框：文本框 + 发送按钮 + /可补全      │
│  │ /hot   │  （回车发送；/ 触发命令补全弹层）            │
│  └ /clip  │                                      │
└──────────┴────────────────────────────────────┘
```

- **左栏**：会话列表 / 历史任务 / 工具按钮（点按钮即在输入框插入 `/命令`，如 `/fast "概念" -n 3`）
- **顶部画布区**：一大片空白，对话消息、任务进度、成片 video 卡片都在这里展开（中间大面积留白）
- **右下发框**：对话框，`/` 触发命令自动补全（读 `/api/tools`），支持自然语言（发出去由后端意图+建议 `/命令`）

### 2.3 数据流

```
发消息(自然语言或/命令) → POST /api/tool/{name}（或 /api/tasks）
  → 返回 {task_id, message, progress_url}
轮询顶部画布 → GET /api/tasks/{id}（1-2s，展示 stage/progress）
完成 → GET /api/video/{task_id} 播放 final.mp4（画布内嵌 video 卡片）
画廊/历史 → 左栏或 /api/tasks?status=completed 网格
```

### 2.4 `/` 斜杠命令（tool 统一分发）

前端发消息若以 `/` 开头，解析 `{name} {args}` → 调 `POST /api/tool/{name}`。
可调用既有工具：

| 命令 | 后端逻辑 | 说明 |
|------|---------|------|
| `/auto 概念 --character X` | 同 `vidance.py auto` | LLM 编剧+生成+后处理 |
| `/fast 概念 [--images-source crawl\|flux] [-n N]` | 同 `fast` | 快速营销号链路 |
| `/hot [-n N]` | 同 `hot` | 爬热点→选题→出片一步到位 |
| `/ask 概念` | v6 dialog | 多轮需求核实，返回补全概念 |
| `/clip 输入 -i "指令" ` | FunClip | 长素材智能裁剪 |
| `/scout` | scout_topics | 选题候选 |
| `/voices` | /api/voices | 列音色 |
| `/video {task_id}` | /api/video | 播放成片 |
| `/help` | — | 列出全部 / 命令 |

前端渲染 `/api/tools` 返回的 `[{name, help, params[{key,type,desc,choices}]}]`，实现输入 `/` 后按名过滤 + 参数提示补全。

---

## 3. 核心流程

```
[1] 用户打开 http://127.0.0.1:8894/ui
[2] 提交概念（可经 v6 dialog 补全）
[3] 列表页轮询进度
[4] 完成后画廊/详情页播放 + 看审片分
[5] 满意 → 手动下载成片上传（发布仍人工，见 v4 §3.5）
```

---

## 4. 接口规格（新增）

### 4.1 只读扩展

```
GET  /api/video/{task_id}          # 视频流（FileResponse final.mp4）
GET  /api/options                  # {voices[], chars[](角色库), luts[], bgms[], effects[], moods[]}
GET  /api/tools                    # [{name, help, params[{key,type,desc,choices}]}] 供 / 命令补全渲染
GET  /ui/*                         # 静态前端（StaticFiles）
```

### 4.2 斜杠命令统一分发

```
POST /api/tool/{name}   body: {args: "概念 --images-source crawl -n 3"}
   → 解析斜杠命令 → 调对应后端（auto/fast/hot/ask/clip/scout/voices/video/help）
   → 返回 {task_id?, message, ...}；任务型返回 task_id 供前端轮询 /api/tasks/{id}
```

### 4.3 复用端点

```
POST /api/tasks  GET /api/tasks/{id}  DELETE /api/tasks/{id}   # 任务 CRUD（/auto 底层）
GET  /api/dashboard                                            # 队列/缩略
POST /api/scout                                                # 选题候选
POST /api/dialog/start  GET/POST /api/dialog/{id}/reply        # v6 对话（/ask 底层）
POST /api/clone_voice  GET /api/clone_voice/{id}               # 声音克隆
POST /api/clip  GET /api/clip/{id}                             # FunClip
```

---

## 5. 验收标准

1. 无 npm/build，浏览器访问 http://127.0.0.1:8894/ui 直接可用
2. 布局呈现：左功能边栏 + 顶部大画布 + 右下发框（仿豆包/智象未来）；窄屏左栏可折叠
3. 发 `/` 出现命令补全弹层（读 /api/tools），选 /fast 填参 → 提交 → 画布内轮询进度 → 成片 video 卡片可播
4. 自然语言消息能唤起后端意图并回复（建议或直接执行）
5. `/video {task_id}` 之类工具可正确分发执行
6. v6 对话（/ask）、画廊/历史在左栏可浏览

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 轮询打爆后端 | 1-2s polling + ETag；可退为 SSE（暂不做） |
| 大量成片视频流占带宽 | 画布卡片用 poster 帧；点击才拉全量视频 |
| 浏览器兼容 | 用 fetch/ES2020 基础语法 + video 原生播放 |
| 界面与后端耦合 | 只依赖 /api/* 契约，前端独立可替换 |
| `/` 命令解析歧义 | /api/tools 给参数 schema，前端强校验后再发 |

---

## 7. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | 静态服务 + 左栏/画布/发框骨架 + options/video/tools 端点 | 骨架 |
| M2 | 对话式消息：发送 + `/` 命令补全 + /api/tool 分发 | 对话 |
| M3 | 任务进度轮询 + 画布内成片 video 卡片 + 画廊/历史 | 画布 |
| M4 | v6 /ask 对话接洽 + scout 选题 + 收尾打磨 | 完整 |

---

## 8. 依赖与前置

- 依赖 v4 M2（/api/tasks）、v6 M4（/api/dialog）
- 复用 core/api_server.py、core/dashboard.py 资产
- 无 npm、无新 Python 依赖（FastAPI 已有）
