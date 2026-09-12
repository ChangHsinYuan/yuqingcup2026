# Vidance v4 设计文档 — 营销号流水线

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v1-v3 见对应 design 文档
>
> **状态：🚧 实现中**（M1 完成：爬虫+选题，M2-M8 待开始）

## 1. 概述

### 1.1 v3 遗留问题

v0-v3 是"单片精修"系统：一次概念 → 一部高质量短片。但要规模化生产内容（营销号、批量短剧），缺：
- **选题自动化**：靠人想概念，无法持续产出
- **并发**：同步一条龙，一次只能跑一片
- **素材获取**：参考图/人声/配乐靠人工备
- **发布**：成片靠人手动上传
- **剪辑自动化**：长素材智能裁剪（去掉口误/冗余）

### 1.2 v4 目标

从"单片工具"演进为"批量内容流水线"：

- **爬热点选题**：自动抓取热点话题 → agent 选题 → 生成概念
- **素材爬取**：参考图、人声样本、配乐自动获取
- **声音克隆生产化**：从爬取人声克隆固定音色库
- **异步任务制**：POST 提交 + GET 轮询，支持并发批量
- **自动发布**：成片自动上传多平台
- **FunClip 智能裁剪**：长素材 ASR 驱动裁剪

### 1.3 v4 范围

| 做 | 不做 |
|----|------|
| 热点爬取（feedparser/yt-dlp/bs4） | 实时热点监听（定时跑即可） |
| agent 选题（热点→概念，多候选打分） | 完全替代人工选题（人可审） |
| 参考图/人声/配乐爬取 | 版权清洗（标注来源，人工审核） |
| 声音克隆生产化（爬人声→CosyVoice 音色） | 实时变声 |
| 异步任务队列（POST/GET，并发批量） | 分布式集群（单机多 GPU 够） |
| 自动发布（YouTube/TikTok/视频号） | 全平台覆盖（先主流） |
| FunClip 长素材智能裁剪 | 专业级剪辑 |
| 批量生成调度（GPU 队列） | — |

### 1.4 核心验证点

1. 热点爬取 + agent 选题能否稳定产出可用概念
2. 异步任务队列并发 N 片时 GPU 调度是否稳定（v0-v3 串行单片）
3. 声音克隆从爬取人声到可用音色的自动化程度
4. 自动发布的稳定性（平台 API/cookie 授权）
5. FunClip 裁剪对长旁白素材的智能程度
6. 批量生产的内容质量是否可接受（vs v0-v3 精修）

---

## 2. 架构设计

### 2.1 v3 → v4 架构演进

```
v3:  人想概念 → 单片精修（同步一条龙）→ 人手动发布

v4:  [爬热点] → agent 选题 → [批量异步任务] → [自动发布]
                        ↑                ↓
              [素材爬取/音色克隆]   [FunClip 裁剪]
```

### 2.2 三层架构（v4）

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v4: 营销号流水线（批量自动生产+发布）            │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(scout/reviewer/publisher)  │
│           core/ scheduler（异步队列 + 批量调度）           │
├──────────────────────────────────────────────────────────┤
│  引擎层   选题: 爬虫(feedparser/yt-dlp/bs4) + LLM         │
│           生成: 复用 v1-v3 全引擎（角色锚/场景锚/后期）    │
│           音色: CosyVoice 克隆生产化                       │
│           裁剪: FunClip (ASR 驱动)                        │
│           发布: 平台 API (YouTube/TikTok/视频号)           │
│           队列: FastAPI + 内存/SQLite 任务队列             │
│           LLM: USTC API                                   │
└──────────────────────────────────────────────────────────┘
```

### 2.3 混合模式（v4 扩展）

| 能力 | 类型 | 归属 |
|------|------|------|
| 热点抓取 + 去噪 | 确定性执行 | **code**（爬虫） |
| 热点→概念（多候选+打分） | 创意决策 | **agent**（scout subagent + LLM） |
| 概念→分镜→生成（复用 v1-v3） | 混合 | **agent + code** |
| 素材爬取（参考图/人声/BGM） | 确定性执行 | **code** |
| 音色克隆生产化 | 确定性执行 | **code** |
| 批量调度（GPU 队列） | 确定性执行 | **code**（scheduler） |
| 发布前审查（合规/质量） | 判断 | **agent**（reviewer + publisher） |
| 自动发布 | 确定性执行 | **code** |
| FunClip 裁剪决策 | 创意决策 | **agent** |

### 2.4 agent 拓扑（v4）

```
[定时触发 / 手动触发]
  │
  ├─ [爬虫] 抓热点 → hot_topics.json
  │
  ├─ task → scout subagent
  │     输入: hot_topics + 账号定位
  │     输出: 概念候选 [{concept, angle, predicted_score}, ...] 排序
  │     职责: 选题 + 预估潜力，不生成
  │
  ├─ director (主控)
  │     ├─ [LLM] 选中概念 → script（复用 v1-v3 编剧）
  │     ├─ [素材爬取] 参考图/人声（按需）
  │     ├─ [生成] 复用 v1-v3 pipeline（异步任务）
  │     ├─ [FunClip] 长旁白素材智能裁剪
  │     ├─ task → reviewer 审片（复用，含合规维度）
  │     └─ task → publisher subagent
  │              输入: 成片 + 平台列表 + 账号凭据
  │              输出: 发布结果 [{platform, url, status}]
  │
  └─ [调度] scheduler 管理并发任务队列
```

### 2.5 异步任务制（v4 核心）

v0-v3 同步阻塞，v4 改异步队列：

```
POST /tasks
  body: { "concept": "...", "options": {...} }
  resp: { "task_id": "xxx", "status": "queued" }

GET /tasks/{task_id}
  resp: { "status": "queued|running|completed|failed", "progress": 0.6, "output": null|"final.mp4" }

GET /tasks?status=running   # 批量查询
```

- **队列**：FastAPI + SQLite 持久化（轻量，无需 Redis）
- **并发**：同时跑 N 片（N = GPU 调度能力，初始 2-3）
- **调度**：scheduler 进程轮询队列，按 GPU 空闲分配任务
- **状态机**：queued → running(generating/post_processing/publishing) → completed/failed

---

## 3. 核心流程

### 3.1 热点选题

```
[1] 爬虫抓热点
      feedparser: RSS 源（微博热搜/知乎热榜/B站热门 RSS 订阅）
      yt-dlp: YouTube 热门视频元数据
      bs4: 网页热点榜单抓取
      → hot_topics.json [{title, source, heat, url, snippet}]

[2] scout subagent 选题
      输入: hot_topics + 账号定位（如"影视解说"/"萌宠"/"情感"）
      LLM 处理: 热点→概念转化 + 角度创新 + 潜力打分
      输出: 概念候选排序 [{concept, angle, reason, predicted_score}]
      → director 选 top-K 进入生成队列
```

### 3.2 素材爬取

```
[参考图]
  LLM 从 concept 提取关键词 → 搜索引擎图片 API / Unsplash / 爬虫
  → input/reference/{task_id}/

[人声样本]（声音克隆生产化）
  按目标音色风格爬取公众人物人声（播报/解说/故事）
  yt-dlp 提取音频 → sox 切 5-10s 纯人声段
  → voices/{voice_name}/prompt.wav + meta.json
  → 自动注册为 cosy-{voice_name} 音色

[BGM]
  按脚本 mood 爬免版税音乐（YouTube Audio Library / ccMixter）
  → assets/bgm/{mood}/
```

### 3.3 声音克隆生产化

v0 手动放 prompt.wav，v4 自动化：

```
[1] 目标音色描述 → 搜索爬取人声音频
[2] yt-dlp 下载 → sox 自动切分（去静音、取纯人声段）
[3] 转 CosyVoice 要求格式（24kHz mono 16bit wav, 5-10s）
[4] 自动转写 prompt_text（faster-whisper STT）
[5] 入库 voices/{name}/ + 注册 cosy-{name}
[6] 质量校验：用样本克隆合成测试句 → 审听
```

### 3.4 FunClip 智能裁剪

长素材（如长旁白录制/长视频素材）用 FunClip 智能裁剪：

```
[长素材.wav/mp4]
  → FunClip (ASR 转写 + 词级时间戳)
  → agent 标注保留/删除片段（按内容语义）
  → FunClip 自动裁剪拼接
  → 裁剪后素材
```

- **场景**：长旁白录制有口误 → FunClip 按语义去口误段
- **场景**：爬取长视频素材 → FunClip 提取关键段做参考
- FunClip 是阿里同生态（基于 FunASR），中文 ASR 强

### 3.5 自动发布

```
[成片] → publisher subagent
  输入: final.mp4 + 平台列表 + 标题/描述/标签（LLM 生成）
  执行:
    YouTube: YouTube Data API（OAuth 授权，上传视频）
    TikTok: 官方 API 或 cookie 模拟上传（参考 MoneyPrinterTurbo）
    视频号: cookie 模拟（无公开 API）
  输出: [{platform, url, status, uploaded_at}]
```

- **凭据管理**：平台 token/cookie 存 `config/publish_credentials.json`（不入 git）
- **参考**：MoneyPrinterTurbo 的 `upload_post.py`（YouTube/TikTok 实现）
- **审核**：发布前 reviewer 审合规（无违规内容/版权问题）

---

## 4. 数据模型（v4 扩展）

### 4.1 任务队列 schema

```json
// tasks.sqlite
{
  "task_id": "20260910_001",
  "concept": "...",
  "status": "running",          // queued|running|completed|failed
  "stage": "generating",        // generating|post_processing|publishing
  "progress": 0.6,
  "options": { "version": "v2", "voice": "edge-xiaoxiao", "duration": 60 },
  "created_at": "...",
  "started_at": "...",
  "completed_at": null,
  "output": null,               // "final.mp4" 完成后填
  "publish_results": null,      // [{platform, url, status}]
  "error": null
}
```

### 4.2 选题记录 schema

```json
{
  "batch_id": "20260910_hot",
  "scraped_at": "...",
  "hot_topics": [...],
  "candidates": [
    {
      "concept": "...",
      "angle": "从猫咪视角讲登月",
      "reason": "萌宠+航天热点交叉，反差感强",
      "predicted_score": 8,
      "source_topic": "神舟发射"
    }
  ],
  "selected": [0, 2, 4]         // 选中进入队列的索引
}
```

---

## 5. 引擎接口规格

### 5.1 异步 API（FastAPI，新）

```
POST /tasks                    # 提交任务
GET  /tasks/{id}               # 查询状态
GET  /tasks?status=running     # 批量查询
DELETE /tasks/{id}             # 取消
GET  /health                   # 健康检查
GET  /hot_topics               # 获取当前热点
POST /clone_voice              # 提交音色克隆
GET  /voices                   # 音色库（复用 v0 TTS /voices）
```

### 5.2 爬虫（新组件）

| 库 | 用途 |
|----|------|
| feedparser | RSS 热点订阅 |
| yt-dlp | 视频元数据/下载 |
| bs4 + lxml | 网页榜单抓取 |
| requests | HTTP |

### 5.3 FunClip（新组件）

```bash
# FunClip CLI
funclip clip --input long.mp4 --text "保留片段语义描述" --output clipped.mp4
# 或 Python API：FunASR 转写 → 语义裁剪
```

### 5.4 发布（新组件）

| 平台 | 方式 | 参考 |
|------|------|------|
| YouTube | Data API v3（OAuth） | MPT `upload_post.py` |
| TikTok | 官方 API / cookie 模拟 | MPT `upload_post.py` |
| 视频号 | cookie 模拟 | 待验证 |

### 5.5 复用 v1-v3 组件

全部生成引擎（FLUX/TripoSplat/Hunyuan3D/RenderSplat/Wan I2V/TTS/RIFE/ffmpeg）、LLM、审片 —— 复用。

---

## 6. GPU 与调度

### 6.1 批量调度

```
scheduler 进程:
  while True:
    running = query_tasks(status="running")
    if len(running) < MAX_CONCURRENT:
      next = query_tasks(status="queued", limit=MAX_CONCURRENT - len(running))
      for task in next:
        assign_gpu(task)  # 按引擎需求分配 GPU
        start_pipeline(task)  # 异步线程
    sleep(5)
```

- **MAX_CONCURRENT**：初始 2（GPU 显存约束），实测调
- **GPU 分配**：任务按版本（v1/v2/v3）用不同引擎组合，scheduler 按空闲分配
- **隔离**：每任务独立 output 目录，meta.json 不串

### 6.2 GPU 共享策略

| 场景 | 策略 |
|------|------|
| 两任务都用 Wan I2V (8189) | ComfyUI 排队，串行执行 |
| 一任务用 FLUX，一用 Wan | 真并行（不同卡） |
| 一任务用 Hunyuan3D | 独占 GPU1 期间不分配第二个 Hunyuan3D 任务 |

---

## 7. 新增 agent / skill

### 7.1 scout subagent（选题）

```
director → task → scout subagent
  输入: hot_topics.json + 账号定位
  输出: 概念候选排序
  职责: 热点→概念转化 + 潜力预估，不生成内容
  模型: deepseek-v4-flash（文本，快）
```

### 7.2 publisher subagent（发布）

```
director → task → publisher subagent
  输入: 成片 + 平台列表 + 凭据
  输出: 发布结果
  职责: 调发布 API/cookie 上传，返回 URL
  不做: 不审片（reviewer 先审）
```

### 7.3 skills

| skill | 用途 |
|-------|------|
| `topic_scouting/SKILL.md` | 选题规范（账号定位匹配/角度创新/打分） |
| `publishing/SKILL.md` | 发布规范（平台差异/标题标签生成/合规） |

---

## 8. 依赖与前置

### 8.1 新增 Python 依赖

| 包 | 用途 |
|----|------|
| feedparser | RSS |
| yt-dlp | 视频下载 |
| bs4 + lxml | 网页抓取 |
| fastapi + uvicorn | 异步 API（已有 TTS 用，复用） |
| FunClip | 智能裁剪 |

### 8.2 新增外部服务

| 服务 | 用途 | 状态 |
|------|------|------|
| YouTube Data API | 发布 | ❌ 需 OAuth 配置 |
| TikTok API/cookie | 发布 | ❌ 需验证 |
| 视频号 cookie | 发布 | ❌ 需验证 |

### 8.3 代码新增

| 文件 | 改动 |
|------|------|
| `core/scheduler.py` | 异步任务队列 + 批量调度 |
| `core/api_server.py` | FastAPI 异步接口 |
| `utils/crawler.py` | 热点/素材爬取 |
| `utils/voice_clone.py` | 声音克隆生产化 |
| `utils/publisher.py` | 多平台发布 |
| `utils/funclip.py` | FunClip 裁剪封装 |
| `.opencode/agents/scout.md` | scout subagent |
| `.opencode/agents/publisher.md` | publisher subagent |
| `.opencode/skills/topic_scouting/SKILL.md` | 选题规范 |
| `.opencode/skills/publishing/SKILL.md` | 发布规范 |

---

## 9. 验收标准

v4 跑通的标志：

1. **热点选题**：爬热点 → scout 选概念 → 生成，无需人工想概念
2. **异步队列**：POST 提交 → GET 轮询，支持 ≥2 任务并发
3. **音色克隆**：爬人声 → 自动克隆 → 可用音色，无需手动备
4. **自动发布**：成片自动上传 ≥1 平台（YouTube/TikTok），返回 URL
5. **FunClip**：长素材智能裁剪可用
6. **批量生产**：一次提交 5 个概念，队列调度全部完成出片
7. **质量**：批量成片质量可接受（vs v0-v3 精修，允许略降）

验收命令（设计）：
```bash
# 提交批量任务
for c in "概念1" "概念2" "概念3"; do
  curl -X POST localhost:8000/tasks -d "{\"concept\":\"$c\"}"
done
# 轮询
curl localhost:8000/tasks?status=running
# 自动选题+生成+发布
python core/scheduler.py --auto-scout --account "影视解说" --publish youtube,tiktok
```

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 热点爬取被反爬/限流 | 选题断供 | 多源 RSS + 间隔抓取 + User-Agent 轮换 |
| scout 选题质量不稳 | 概念不可用 | 多候选打分 + 人工可审环节 |
| 平台发布 API 变动/封号 | 发布失败 | cookie 模拟降级；发布频率限流 |
| 声音克隆音色侵权 | 法律风险 | 只克隆公众/授权人声；标注来源 |
| 并发致 GPU OOM | 任务失败 | MAX_CONCURRENT 保守；scheduler 监控显存 |
| FunClip 中文裁剪不准 | 裁剪不当 | FunASR 中文强；人工兜底 |
| 批量内容同质化 | 平台降权 | scout 角度创新 + 风格多样化 |
| 长期运行的内存泄漏 | 服务崩 | 任务隔离进程/定时重启 |

---

## 11. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | 爬虫 + 热点抓取 + scout subagent | 自动选题 |
| M2 | 异步任务队列（FastAPI + SQLite）+ scheduler | 并发调度 |
| M3 | 声音克隆生产化（爬人声→音色） | 自动音色 |
| M4 | FunClip 智能裁剪接入 | 长素材处理 |
| M5 | 发布模块（YouTube/TikTok）+ publisher subagent | 自动发布 |
| M6 | 素材爬取（参考图/BGM） | 素材自动化 |
| M7 | 批量端到端联调（5 概念并发） | 流水线闭环 |
| M8 | 无人值守批量生产验证 | v4 上线 |

> 依赖 v1-v3 完成。v4 是工程化阶段，引擎复用，重点在调度/爬虫/发布/异步。
