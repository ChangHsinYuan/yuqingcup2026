# Vidance v4 设计文档 — 营销号流水线

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v1-v3 见对应 design 文档
>
> **状态：🚧 实现中**（M1+M2+M3+M4+M5 完成：爬虫+选题+异步任务队列+声音克隆生产化+FunClip 智能裁剪+素材爬取，M6-M7 待开始）

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
- **FunClip 智能裁剪**：长素材 ASR 驱动裁剪

### 1.3 v4 范围

| 做 | 不做 |
|----|------|
| 热点爬取（feedparser/yt-dlp/bs4） | 实时热点监听（定时跑即可） |
| agent 选题（热点→概念，多候选打分） | 完全替代人工选题（人可审） |
| 参考图/人声/配乐爬取 | 版权清洗（标注来源，人工审核） |
| 声音克隆生产化（爬人声→CosyVoice 音色） | 实时变声 |
| 异步任务队列（POST/GET，并发批量） | 分布式集群（单机多 GPU 够） |
| FunClip 长素材智能裁剪 | 专业级剪辑 |
| 批量生成调度（GPU 队列） | 自动发布（人精挑细选，手动上传） |

### 1.4 核心验证点

1. 热点爬取 + agent 选题能否稳定产出可用概念
2. 异步任务队列并发 N 片时 GPU 调度是否稳定（v0-v3 串行单片）
3. 声音克隆从爬取人声到可用音色的自动化程度
4. FunClip 裁剪对长旁白素材的智能程度
5. 批量生产的内容质量是否可接受（vs v0-v3 精修）

---

## 2. 架构设计

### 2.1 v3 → v4 架构演进

```
v3:  人想概念 → 单片精修（同步一条龙）→ 人手动发布

v4:  [爬热点] → agent 选题 → [批量异步任务] → 人精选后手动发布
                         ↑                ↓
               [素材爬取/音色克隆]   [FunClip 裁剪]
```

### 2.2 三层架构（v4）

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v4: 营销号流水线（批量自动生产，人精选发布）      │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(scout/reviewer)            │
│           core/ scheduler（异步队列 + 批量调度）           │
├──────────────────────────────────────────────────────────┤
│  引擎层   选题: 爬虫(feedparser/yt-dlp/bs4) + LLM         │
│           生成: 复用 v1-v3 全引擎（角色锚/场景锚/后期）    │
│           音色: CosyVoice 克隆生产化                       │
│           裁剪: FunClip (ASR 驱动)                        │
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
  │     └─ task → reviewer 审片（复用，含合规维度）
  │
  └─ [调度] scheduler 管理并发任务队列
```
（发布不做：成片人工精选后手动上传）

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
- **状态机**：queued → running(generating/post_processing) → completed/failed

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

### 3.5 发布（不做）

发布不在 v4 范围：成片人工精挑细选后手动上传。流水线产出停在 `output/{task_id}/final.mp4` + meta.json，人从仪表盘（`/api/dashboard`）挑片看片，满意的手动发平台。

### 3.6 快速营销号链路（图片 + RIFE，M6 新增）

v4 之外的快链：**跳过视频模型**（Wan/I2V），只用静态图片 + 运镜动效 + RIFE 插帧组成视频，主打分钟级快速出片。

```
[1] 爬热点（复用 M1 crawler）
[2] LLM 选题 + 出文案（复用 scout/编剧；可人工给文案）
[3] 选图：LLM 按文案/关键词从爬取参考图挑 N 张（复用 M5 asset_crawler）
[4] 运镜：ffmpeg zoompan/kenburns 对每张静态图做推拉摇移（2-4s/张）
[5] RIFE 插帧：图片动效帧 → RIFE 补帧平滑（复用 v2 rife.py）
[6] 语音/字幕/BGM 照常复用：TTS 配音 + STT 字幕 + select_bgm
[7] 合成成片（复用 compose/yuv420p 约定）
```

- **动机**：营销号追求速度与量，视频模型生成慢（10+ 分钟/段）又受 GPU/显存约束；纯图片流几无 GPU 视频模型负担，出片分钟级
- **主色调**：静态图 + 模拟运镜（zoompan）+ RIFE 平滑 →"伪视频"，视觉上可接受
- **可选增强**：每张图先用 FLUX 风格化/扩图再成片（质量更好但慢；默认跳过，保持 min 级）
- **跳过视频模型的约定**：本链路缺省不调 Wan/I2V，`--video-model` 显式开启才用真视频模型逐段替换静态图段

---

## 4. 数据模型（v4 扩展）

### 4.1 任务队列 schema

```json
// tasks.sqlite
{
  "task_id": "20260910_001",
  "concept": "...",
  "status": "running",          // queued|running|completed|failed
  "stage": "generating",        // generating|post_processing
  "progress": 0.6,
  "options": { "version": "v2", "voice": "edge-xiaoxiao", "duration": 60 },
  "created_at": "...",
  "started_at": "...",
  "completed_at": null,
  "output": null,               // "final.mp4" 完成后填
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

### 7.2 skills

| skill | 用途 |
|-------|------|
| `topic_scouting/SKILL.md` | 选题规范（账号定位匹配/角度创新/打分） |

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

（无发布相关服务；v4 全部外部依赖在 v1-v3 已有：USTC LLM、ComfyUI、CosyVoice）

### 8.3 代码新增

| 文件 | 改动 |
|------|------|
| `core/scheduler.py` | 异步任务队列 + 批量调度 |
| `core/api_server.py` | FastAPI 异步接口 |
| `utils/crawler.py` | 热点/素材爬取 |
| `utils/voice_clone.py` | 声音克隆生产化 |
| `utils/funclip.py` | FunClip 裁剪封装 |
| `.opencode/agents/scout.md` | scout subagent |
| `.opencode/skills/topic_scouting/SKILL.md` | 选题规范 |

---

## 9. 验收标准

v4 跑通的标志：

1. **热点选题**：爬热点 → scout 选概念 → 生成，无需人工想概念
2. **异步队列**：POST 提交 → GET 轮询，支持 ≥2 任务并发
3. **音色克隆**：爬人声 → 自动克隆 → 可用音色，无需手动备
4. **FunClip**：长素材智能裁剪可用
5. **批量生产**：一次提交 5 个概念，队列调度全部完成出片
6. **质量**：批量成片质量可接受（vs v0-v3 精修，允许略降）

验收命令（设计）：
```bash
# 提交批量任务
for c in "概念1" "概念2" "概念3"; do
  curl -X POST localhost:8000/tasks -d "{\"concept\":\"$c\"}"
done
# 轮询
curl localhost:8000/tasks?status=running
# 自动选题+生成
python core/scheduler.py --auto-scout --account "影视解说"
```

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 热点爬取被反爬/限流 | 选题断供 | 多源 RSS + 间隔抓取 + User-Agent 轮换 |
| scout 选题质量不稳 | 概念不可用 | 多候选打分 + 人工可审环节 |
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
| M5 | 素材爬取（参考图/BGM） | 素材自动化 |
| M6 | 快速营销号链路（图片+运镜+RIFE，跳过视频模型） | ✅ 分钟级快速出片 |
| ~~M7 批量端到端联调~~ | 降级：单任务已验证，批量并发价值低 | — |
| ~~M8 无人值守批量生产验证~~ | 跳过：发布由人精挑细选手动上传 | — |

> 依赖 v1-v3 完成。v4 是工程化阶段，引擎复用，重点在调度/爬虫/异步。发布由人工精挑细选手动完成，不在流水线范围。
> M6 之后新增（2026-09-14）：快速营销号链路，见 §3.6。原 M6 批量联调 / M7 无人值守经确认跳过（单任务全链路已验证即算达标）。

### M1 完成详情（2026-09-12）

- `utils/crawler.py`（~250行）：B站 API + 微博热搜 + 知乎热榜 + 百度热搜 + 通用 RSS + YouTube trending（yt-dlp）
- `llm.scout_topics()`：热点→概念候选排序（predicted_score 1-10，含 concept/angle/reason/source_topic）
- `.opencode/agents/scout.md` + `.opencode/skills/topic_scouting/SKILL.md`
- 端到端验证：45 条热点（B站/微博/知乎各 15）→ 5 个概念候选（9/8/8/7/7 分）

### M2 完成详情（2026-09-12）

- `core/scheduler.py`（~300行）：`TaskQueue`（SQLite 持久化，submit/get/list/cancel/update）+ `Scheduler`（线程轮询，subprocess 调用 `vidance.py auto --task-id`）+ CLI（run/submit/list/status/cancel/dashboard）
- `core/api_server.py`（~130行）：FastAPI :8894，POST/GET/DELETE /api/tasks + /api/health + /api/scout + /api/dashboard
- `core/dashboard.py`（~200行）：自包含 HTML 仪表盘，base64 嵌入缩略图，队列统计+任务列表+选题批次，30s auto-refresh
- `pipeline.py` + `vidance.py` 修改：`--task-id` 参数传入，调度器与 pipeline 共享 task_id
- 端到端验证：3 任务提交 → 串行执行（max_concurrent=1）→ 3 成片
  - 柴犬在樱花树下打坐冥想（4镜, 12.7s, 832×480, warm LUT, calm BGM）
  - 赛博朋克城市的霓虹雨夜街道（3镜, 11.4s, 1280×704, cool LUT, mysterious BGM）
  - 小厨娘在魔法森林里煮蘑菇汤（4镜, 11.9s, 832×480）
- 可视化产出：`output/dashboard.html`（队列仪表盘）+ `output/m2_results.html`（M2 成片展示页，含角色参考图+合成参考帧+视频信息）

### M3 完成详情（2026-09-13）

- `utils/voice_clone.py`（~420行）：`VoiceCloner` 完整克隆流水线
  - **搜索**：B站 `x/web-interface/search/type` API + 随机 `buvid3` cookie 过反爬（yt-dlp `bilisearch` 被 412 拦截），按播放量降序 + 时长过滤（≥180s 保证有足够人声）
  - **下载**：yt-dlp bestaudio，文件名按 bvid 隔离（防同名跳过下载返回旧候选音频）
  - **切段**：faster-whisper VAD（扫描前 300s）→ 过滤纯 ♪ 器乐段 → 相邻合并（间隔<0.4s，解说语速连续会合并成巨块）→ 候选 = 5-12s 块 + 巨块取前 8s/中间 8s 窗口 → 跳过片头 3s → 按接近 8s 排序 → 质量门（ffmpeg volumedetect mean_volume > -32dB）
  - **转格式**：ffmpeg → 24kHz mono 16bit wav（CosyVoice 要求）
  - **转写**：faster-whisper STT 自动生成 prompt_text
  - **注册**：`voices/{name}/prompt.wav` + `meta.json`（prompt_text/desc/来源）→ TTS server 扫描注册 `cosy-{name}`
  - **测试**：TTS 合成测试句 → `voice_samples/cosy-{name}__{desc}.wav` 试听
- `tts_server.py`：加 `POST /voices/reload` 热加载端点（免重启注册新音色）
- `api_server.py`：加 `POST /api/clone_voice`（异步线程，下载+VAD+转写 1-3 分钟）+ `GET /api/clone_voice/{job_id}` + `GET /api/voices`
- 端到端验证（3 音色全通，单音色 21-30s）：
  - `cosy-xinwen1` 新闻播音-男声（源：B站新闻速递，prompt_text="1.新华社9月11日报道..."）
  - `cosy-jieshuo1` 影视解说-男声（源：《消失在第七街》解说）
  - `cosy-jilupian1` 纪录片-男声（API 异步端点克隆）
  - STT 转写回验克隆合成内容一致；注册音色可按名直接调用（无需传 prompt_wav）
- 调试修复：①测试合成 synthesize 需传 output_path；②解说风语音连续 → 音乐过滤下移到段级别（只跳纯 ♪ 段）+ 巨块取多窗口；③yt-dlp 同名跳过下载 → 文件名按 bvid 隔离

### M4 完成详情（2026-09-13）

- `utils/funclip.py`（~330行）：`FunClip` 智能裁剪器
  - **转写**：FunASR `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch`（~1.1GB，缓存于 ~/.cache/modelscope/），`generate(batch_size_s=300, return_raw=True)` 返回字级 text+timestamp（[[130,350],[350,510],...] ms 段）
  - **聚合**：字级时间戳按字间停顿（gap>300ms 或累计>1500ms）聚合成句
  - **裁剪**：ffmpeg concat demuxer，`_has_video()` 自动探测视频/纯音频（视频 yuv420p+High profile，音频 pcm_s16le wav）
  - **语义**：`smart_clip()` LLM 逐句 keep/drop 判定 → 合并连续保留段（吸收 <400ms 句间静音）→ 裁剪
  - **GPU 修复**：满卡（GPU0 被 H3 占满 48.4G）连 CUDA context 都建不出来（`cudaMemGetInfo` 直接抛 OOM），`device='auto'` 逐卡 try/except 探测跳过建不了 context 的卡 + 选空闲显存最多的卡；CLI 默认 `cuda:0` 触碰满卡即死是之前 OOM 之谜的根因
  - CLI 三子命令：transcribe（--sentences）/ clip（--segments "0-3,5-8"）/ smart（-i 指令，--no-llm）
  - `api_server.py`：加 `POST /api/clip`（异步线程）+ `GET /api/clip/{job_id}`
- 端到端验证：
  - 4 句水母剧本（subtitle.srt）拼 12.6s 长音频 → 指令"只要讲光的句子" → LLM 保留 2 句（"光从寂静中诞生"+"黑暗拥抱它它也点亮黑暗"，语义含"点亮之光"）drop 2 句 → 输出 3.88s，ASR 转写回验一致
  - API 异步端点：clip_20260913_192833 completed，keep=[T,F,F,F,T,T,F,F]，output 落 output_dir

### M5 完成详情（2026-09-13）

- `utils/asset_crawler.py`（~290行）：`AssetCrawler` 素材爬取器（人声样本已在 M3 voice_clone.py 完成）
  - **参考图**：必应图片 async 接口（`cn.bing.com/images/async`，直连可达）解析 `m="{...}"` JSON 属性取原图直链 murl → 下载 + magic bytes 校验（JPEG/PNG/WebP/GIF/BMP）+ ≥8KB + md5 去重；百度 acjson 接口需真 cookie（antiFlag 拦截）已弃用
  - **关键词**：LLM concept → 图片搜索关键词（chat_json，无 llm 退化为 concept 原文）
  - **BGM**：incompetech.com（Kevin MacLeod CC BY，直连可达）pieces.json（1442 曲目，固化到 bgm/pieces.json）按 feel 标签搜曲 → 下载 → loudnorm I=-20:TP=-1.5:LRA=11 → bgm/{mood}.wav（select_bgm custom_dir 自动生效）
  - **修正**：pieces.json 时长字段是 `length`（'HH:MM:SS' 格式）非 duration（读错字段曾把 130 首 epic 全滤掉）；filename 自带 `.mp3` 后缀不可重复拼（拼重 404）；按"越接近 60s 越好"排序（避免 200MB 巨物+太短不够用）
  - CLI 四子命令：keywords（LLM 提取）/ images（搜图下载）/ refs（concept→关键词→图片一条龙）/ bgm（--list / 下载）
- 端到端验证：
  - concept"雪山上的日出小狐狸" → LLM 3 关键词（雪山之巅金色晨曦狐狸剪影/雪峰日出霞光狐狸遥望/雪山日出橘色天幕狐狸侧影）→ 6 张参考图（19-129KB，jpg/png 正确识别）
  - `bgm epic --list` → 5 首候选（58-62s，Fanfare for Space/Achilles/Strength of the Titans...）→ 爬取 epic.wav（61.4s 10.8MB loudnorm）

### M6 完成详情（2026-09-14）

- `utils/fastline.py`（`FastLine` 类 ~375 行）：快速营销号链路器
  - **旁白**：`plan_narration()` LLM 把概念拆 N 段旁白 + 每段运镜（pan/zoom-in/zoom-out），失败退化为标点切分
  - **运镜**：`_kenburns()` ffmpeg zoompan（Ken Burns 推拉摇移，3 动效）静态图→2-4s 伪动态段，输出 832×480 yuv420p h264
  - **选图**：`collect_images()` 用 `--images` 目录或必应爬取（复用 M5 asset_crawler）
  - **复用**：逐段 TTS 旁白；`compose()` crossfade 拼接 + 估算时间轴字幕；`PostProcessor.run_all()` 施加 LUT/BGM（自动 LLM 选或 override）+ 可选 STT
  - **慢动作**：`--slowmo N` 复用 RIFEClient.slowmo（GPU2 可选，默认关）
  - `vidance.py` 加 `fast` 子命令：`python core/vidance.py fast "概念" [--images dir] -n N --voice --bgm --lut --stt --slowmo`
- 端到端验证（全链路各分支）：
  - `fast "赛博朋克霓虹雨夜的机械狐狸" -n 3`：3 关键词→必应爬 3 图→3 段旁白+TTS→Ken Burns→crossfade→9.1s 成片，55s 完成（含爬网）
  - 自供图（--images）2 段：LLM 旁白+TTS+zoompan 全通；auto LUT（warm）+ auto BGM（calm 取 custom_dir）自动选择
  - `--bgm epic`（custom_dir bgm/epic.wav）+ `--lut warm` 生效
  - `--stt`：faster-whisper 转写→烧录 STT 字幕（2 段，时间戳正确）
  - `--slowmo 2`：RIFE 插帧 36→71 帧（小样验证），输出有效
  - **修正**：`--bgm` choices 原限 5 种，加 `epic`（bgm/epic.wav 已存在）；"RIFE 组成视频"指 zoompan 动效（本就 24fps 平滑），`--slowmo` 为可选 RIFE 增强，均跳过 Wan/I2V 视频模型
