# Vidance v7 设计文档 — 前端

> 用户编号：**v8**。仓库版本号 v7（紧接 v6 之后）。
> 状态：🎯 待开发。目标：Web 界面，摆脱纯 CLI，营销号运营看得见、点得动。

---

## 1. 概述

### 1.1 v6 遗留 / 现状

- 全部操作靠 CLI（vidance.py / scheduler.py），运营人员上手门槛高
- 成片散落 output/ 目录，无集中浏览画廊
- 无可视化任务提交、状态、审片、对话补全入口

### 1.2 v7 目标

- 自包含 Web 界面（FastAPI 静态服务 + vanilla JS），**零 npm 构建链**（npm/GitHub 被墙）
- 页面：任务表单（概念/角色/音色/时长/特效）→ 列表/详情/进度轮询 → 成片画廊（video 播放 + 审片分 + meta）→ v6 对话 UI → 选题候选/一键克隆
- 后端复用 + 少量新只读端点

### 1.3 v7 范围

- 静态服务托管自包含 HTML/CSS/JS（单页 + 原生 fetch，与 dashboard.py 同风格）
- 新端点：`GET /api/video/{task_id}`（视频流）、`GET /api/options`（音色/角色/LUT/BGM/特效枚举）
- 复用：/api/tasks（提交/查询）、/api/dashboard、/api/scout、/api/dialog/*（v6）
- 移动端可用（营销号运营常手机看）

### 1.4 核心验证点

- 浏览器打开即用，无 npm/build 步骤
- 提交流程端到端：表单 → 任务 → 轮询进度 → 成片可播
- v6 对话 UI 三端点连通
- 画廊可播放任意已完成成片

---

## 2. 架构设计

### 2.1 技术选型

- **零依赖自包含**：单 HTML 内嵌 CSS+JS（或少量分离文件），原生 fetch + 轮询
- 后端：扩展现有 `core/api_server.py`（FastAPI :8894），`StaticFiles` 挂 /ui
- 与 `core/dashboard.py`（v4 HTML 仪表盘）风格统一

### 2.2 页面结构

```
/     → 索引：任务提交表单 + 队列概览
/list → 任务列表（状态/进度/成片缩略）
/task/{id} → 详情（meta + 审片分 + 播放器）
/gallery → 成片画廊（网格 video 卡片，按完成时间排序）
/dialog → v6 对话补全 UI（概念 → 追问 → 提交）
/scout → 选题候选 + 一键克隆成任务
```

### 2.3 数据流

```
表单 → POST /api/tasks (options 含 effects/dialog_id)
轮询 → GET /api/tasks/{id} (1-2s interval，页面显示 stage/progress)
完成 → GET /api/video/{task_id} 播放 final.mp4
画廊 → GET /api/tasks?status=completed 网格展示
```

### 2.4 权限

- 内网 127.0.0.1 单机，不做鉴权（与现 API 一致），注释清楚

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
GET  /api/options                  # {voices[], chars[](角色库), luts[], bgms[], effects[]}
GET  /ui/*                         # 静态前端（StaticFiles）
```

### 4.2 复用端点

```
POST /api/tasks  GET /api/tasks/{id}  DELETE /api/tasks/{id}   # 任务 CRUD
GET  /api/dashboard                                            # 队列/缩略
POST /api/scout                                                # 选题候选
POST /api/dialog/start  GET/POST /api/dialog/{id}/reply        # v6 对话
POST /api/clone_voice  GET /api/clone_voice/{id}               # 声音克隆
POST /api/clip  GET /api/clip/{id}                             # FunClip
```

---

## 5. 验收标准

1. 无 npm/build，浏览器直接访问可用
2. 提交流程端到端（表单 → 队列 → 进度 → 成片播放）
3. 画廊可播放全部已完成任务 final.mp4
4. v6 对话 UI 可用（创建/追问/补全/提交）
5. 移动端布局可用（响应式，卡片网格自适应）

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 轮询打爆后端 | 1-2s polling + ETag；可退为 SSE（暂不做） |
| 大量成片视频流占带宽 | 画廊缩略用 poster 帧；详情才拉全量视频 |
| 浏览器兼容 | 用 fetch/ES2020 基础语法 + video 原生播放 |
| 界面与后端耦合 | 只依赖 /api/* 契约，前端独立可替换 |

---

## 7. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | 静态服务挂载 + options/video 端点 | 骨架 |
| M2 | 任务表单 + 列表 + 进度轮询 | 任务页 |
| M3 | 成片画廊 + 详情播放 + 审片分展示 | 画廊 |
| M4 | v6 对话 UI + scout 选题入口 | 综合 |

---

## 8. 依赖与前置

- 依赖 v4 M2（/api/tasks）、v6 M4（/api/dialog）
- 复用 core/api_server.py、core/dashboard.py 资产
- 无 npm、无新 Python 依赖（FastAPI 已有）
