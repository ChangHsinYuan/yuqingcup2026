# Vidance v9 设计文档 — 机器人网关（多 IM）

> 仓库版本号 v9（紧接 v8 之后）。
> 状态：🎯 待开发。目标：把营销号流水线接入聊天工具**网关层**（企业微信 / 飞书 / 钉钉 / Slack / Telegram）——
> 结果推送、任务提醒、简单交互触发，让运营在手机上就能看进度、接成片、下指令。

---

## 1. 概述

### 1.1 动机

- 成片/任务结果都停在 CLI + Web 前端（v7），运营大多在手机上
- 想要"跑完任务 → IM 通知我；在 IM 里回一句也能触发/查询"
- 各 IM 都提供**官方机器人 Webhook / 回调**，做成统一网关层比逐个硬编码更好维护

### 1.2 v9 目标

- **抽象统一网关接口** `Notifier`（send_text/send_markdown/send_link），后接各 IM adapter
- **推送**：任务完成/失败/耗时 → 推送成片缩略+链接（v7 UI 的 /api/video/{task_id}）
- **查询/触发**（进阶）：各 IM 回调 → 文本解析成 `/` 命令 → 复用 v7 `/api/tool/{name}` → 回执
- 只做"IM <-> vidance"薄层，逻辑全复用既有 pipeline

### 1.3 v9 范围

- `utils/notify/`（统一网关）：
  - `base.py`：`Notifier` 抽象（send_text/send_markdown/send_link/send_image）
  - `wecom.py`：企业微信机器人 Webhook + 应用回调（V3 加解密）
  - `feishu.py`：飞书自定义机器人 Webhook（text/post/interactive 卡片）+ 事件回调（challenge 校验 + 加解密）
  - `dingtalk.py`：钉钉群机器人 Webhook（markdown actionCard）
  - `slack.py` / `telegram.py`（可选）：Bot API
  - `factory.py`：按 config 选/组合多个通道（可同时推微信+飞书）
- 与 v4 任务系统挂钩：任务完成 post-hook 推送
- config.json 加 `notify` 节（启用哪些通道 + 各自 token/webhook）

### 1.4 核心验证点

- 配 webhook 后任务完成能把成片链接/状态推送到（企业微信 / 飞书）群
- 多通道可同时开启（一次推多个 IM）
- 至少一个 IM 的回调收到 `/status`、`/fast "概念"` 能正确触发 tool 并回执
- 推送失败不阻塞主流程（仅告警日志）

---

## 2. 架构设计

### 2.1 统一网关层

```
                 ┌─────────────── Notifier (base) ───────────────┐
pipeline/scheduler│   send_text / send_markdown / send_link / image │
   任务完成 hook   └───────┬───────────┬───────────┬──────────────┘
                          ▼           ▼           ▼
                      WecomAdapter  FeishuAdapter  DingAdapter ...
                          │           │           │
                     企微 webhook  飞书 webhook   钉钉 webhook
                          │           │           │
                          ▼           ▼           ▼
                       IM 群/应用    IM 群         IM 群
```

- `factory.build_notifiers(config)` → 返回启用的 adapter 列表；`notify_all()` 并发推、单个失败不影响其余

### 2.2 消息流

```
pipeline 任务完成
   → v4 scheduler post-hook
   → NotifyManager.notify_task(task)  [并发推所有启用通道]
   → 各 adapter 组装 markdown/卡片(标题+耗时+成片链接 /api/video/{id})
   → IM Webhook → 群/应用

(进阶回调，各 IM 一套)
IM 用户消息 → 回调 URL(校验/解密) → gateway 文本解析(/命令)
   → v7 POST /api/tool/{name} → 结果转 markdown 回发
```

### 2.3 各 IM 差异（adapter 内处理）

| IM | 主动推送 | 消息体 | 回调 | 备注 |
|----|---------|-------|------|------|
| 企业微信 | 群机器人 Webhook / 应用消息 API | text/markdown/news | 应用回调（V3 加解密+签名） | 官方、稳定，优先 |
| 飞书 | 自定义机器人 Webhook | text/post/interactive 卡片 | 事件订阅（challenge + 加解密） | 卡片交互丰富 |
| 钉钉 | 群机器人 Webhook | markdown / actionCard | 机器人回调（加签） | 需安全设置（加签/关键词/IP） |
| Slack | Bot Token API | blocks/attachments | Events API | 可选 |
| Telegram | Bot API | Markdown/HTML | getUpdates/webhook | 可选 |

- **个人微信**：需 hook/登录态（易封号），不做，仅文档提及。

---

## 3. 核心流程

```
[1] config 配 notify.{wecom,feishu,...}
[2] v9 任务完成 → NotifyManager.notify_task() 并发推送各 IM
[3] 组装 markdown {标题, 时长, 成片链接, 来源热搜}，推送成功/失败仅告警
[4] (回调) 用户发 "/status" 或 "/fast app" → 对应 adapter 解签 → 调 tool → 回发结果
```

---

## 4. 接口规格

### 4.1 config.json

```json
"notify": {
  "enabled": true,
  "channels": ["wecom", "feishu"],
  "wecom": {
    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
    "token": "", "aes_key": "", "corp_id": "", "agent_id": ""
  },
  "feishu": {
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "app_id": "", "app_secret": "", "verification_token": "", "encrypt_key": ""
  },
  "dingtalk": { "webhook_url": "", "secret": "" }
}
```

### 4.2 utils/notify/base.py

```
class Notifier:
    def send_text(self, text): ...
    def send_markdown(self, md): ...
    def send_link(self, url, title, text=''): ...
    def send_image(self, path): ...
```

### 4.3 utils/notify/factory.py

```
build_notifiers(config) -> list[Notifier]     # 按 config.channels 构建
NotifyManager.notify_task(task_meta)          # 并发推所有通道，单个失败不阻断
```

### 4.4 回调网关（可选，各 IM 一个回调路径）

```
POST /api/notify/{channel}/callback    # wecom/feishu/dingtalk...
  校验/解密 → 解析文本(/命令) → v7 /api/tool/{name} → 回执
```

---

## 5. 验收标准

1. 配好 webhook 后，任一任务完成 → 企业微信 **和** 飞书都收到成片通知（含 /api/video 链接）
2. 多通道并发推送，单通道失败不影响其余；失败仅日志
3. （进阶）回调收到 `/status` 回当前队列；`/fast "概念"` 触发并回发 task_id
4. config `notify.enabled=false` 时零影响

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| webhook 不可达/频率限制 | 失败重试 1 次 + 告警日志，不阻塞主流程 |
| 各 IM 签名/加解密差异大 | adapter 隔离差异，统一 base 接口 + factory |
| 钉钉安全设置（加签/关键词） | 支持 secret 加签 + 关键词前缀 |
| 个人微信封号风险 | 只用官方 webhook/回调，不做个人微信 hook |
| 敏感内容 | 推送仅成片链接/状态，不含 key |

---

## 7. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | `notify/base.py` + `wecom.py` + config | 企微推送 |
| M2 | `feishu.py`（+dingtalk）adapter + factory 多通道 | 多 IM 推送 |
| M3 | scheduler/api 任务完成 post-hook 接入 | 自动通知 |
| M4 | 回调网关（解密→文本→tool 分发→回执，至少一个 IM） | 交互 |

> M4 依赖 v7 的 `/api/tool/{name}`；M1-M3 不依赖 v7 可先做。

---

## 8. 依赖与前置

- 依赖 v4 scheduler（post-hook）、v7 `/api/tool/{name}`（M4）
- 需各 IM 机器人 webhook/应用凭证；无本地新依赖（requests/urllib + 加解密用标准库/hashlib/AES 库）
- 抽象 `Notifier` 接口，后续加新 IM 只需新增 adapter

