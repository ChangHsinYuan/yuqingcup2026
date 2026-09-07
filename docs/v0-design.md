# Vidance v0 设计文档 — 有声短片 MVP

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)

## 1. 概述

### 1.1 目标

跑通一条 **概念→有声成片** 的 T2V 短片端到端流水线，验证 opencode agent 编排本地生视频 + TTS 引擎的可行性。

- **输入**：一句中文概念（如"一只猫在月球上跳舞"）
- **输出**：10-30 秒有声 mp4 短片（2-5 个镜头拼接 + 配音 + 字幕）+ 元数据 JSON
- **全程无需人工干预**：agent 自主编剧、生成、配音、审片、后处理

### 1.2 v0 范围

| 做 | 不做（留给后续版本） |
|----|---------------------|
| LLM 编剧（概念→分镜脚本，含旁白文本） | 角色一致性（v1，3DGS 锚定） |
| 每镜独立 T2V 生成 | I2V / 图生视频（v1） |
| CosyVoice 2 配音（旁白→语音） | 音色克隆生产化（v4） |
| 字幕生成（优先用 TTS 时间戳） | STT 转写（v2+，时间戳不够时引入） |
| 多模态 LLM 逐镜审片 | 3D 重建 / 多视角（v3） |
| 审片不通过自动重做 | 光流插帧软过渡（v2） |
| ffmpeg/moviepy 拼接 + 配音合成 + 字幕 + 转场 | 配乐 / 调色（v2） |
| 元数据完整记录 | 爬热点 / 营销号流水线（v4） |

### 1.3 核心验证点

1. opencode agent 能否稳定调度多步骤流水线（编剧→生成→配音→审片→后处理）
2. agent 决策点与硬编码骨架的边界是否清晰可控
3. 多模态 LLM 审片闭环能否有效拦截低质量输出
4. ComfyUI workflow JSON 模板 + 参数注入机制是否好用
5. CosyVoice 时间戳是否足够支撑字幕对齐（决定是否需引入 STT）

---

## 2. 架构设计

### 2.1 三层架构

```
┌─────────────────────────────────────────────┐
│  产品层   v0: 有声短片  →  v1: 角色一致性   │
├─────────────────────────────────────────────┤
│  编排层   opencode agent runtime            │
│           director + subagents + skills     │
│           core/ Python 流水线骨架（混合模式）│
├─────────────────────────────────────────────┤
│  引擎层   Wan T2V (8189)   CosyVoice (9880) │
│           LLM (USTC)       ffmpeg + moviepy │
└─────────────────────────────────────────────┘
```

### 2.2 混合模式（agent + code 分工）

**原则**：确定性流程硬编码，创意/判断决策交 agent。

| 能力 | 类型 | 归属 |
|------|------|------|
| 概念→分镜脚本（含旁白文本） | 创意决策 | **agent**（director + LLM） |
| scene_desc→英文 video_prompt | 创意决策 | **agent**（director + LLM） |
| 调 ComfyUI 生成视频 | 确定性执行 | **code**（core/ + utils/comfy_api） |
| 旁白文本→配音音频 | 确定性执行 | **code**（core/ + utils/tts） |
| 抽关键帧 | 确定性执行 | **code**（utils/ffmpeg_tools） |
| 审片打分+反馈 | 多模态判断 | **agent**（reviewer subagent + sonnet-4-6） |
| 不通过→重做决策 | 决策 | **agent**（director 根据 reviewer 反馈） |
| 拼接 + 配音合成 + 字幕 + 转场 | 确定性执行 | **code**（utils/ffmpeg_tools） |
| 元数据记录 | 确定性执行 | **code**（core/） |

### 2.3 agent 拓扑：director + subagents

```
director (主控 agent)
  │
  ├─ [LLM 调用] 编剧：concept → script（含每镜 narration 旁白文本）
  ├─ [LLM 调用] prompt 优化：scene_desc → english video_prompt
  ├─ [tool 调用] utils/comfy_api.generate_t2v()  × 每镜
  ├─ [tool 调用] utils/tts.synthesize()  旁白 → 配音音频（带时间戳）
  ├─ [tool 调用] utils/ffmpeg_tools.extract_frames()
  │
  ├─ task ──→ reviewer (subagent)
  │            └─ [LLM 多模态] sonnet-4-6 读帧 → {score, feedback, pass}
  │
  ├─ [决策] 不通过 → 调整 prompt + 新 seed 重做（≤2 次）
  └─ [tool 调用] utils/ffmpeg_tools.compose()
        → 拼接片段 + 合并配音 + 烧录字幕(用TTS时间戳) + 转场 → 成片
```

- **director**：唯一与用户交互的 agent，全流程主控。用 `llm.chat()` tool 做编剧/prompt 优化，用 `comfy_api`/`ffmpeg_tools` tool 做执行，用 `task` tool 派审片子任务给 reviewer。
- **reviewer**：专职审片 subagent，接收关键帧 + scene_desc，调 sonnet-4-6 多模态读图，返回结构化判断。不直接生成/修改视频。

### 2.4 同步执行

v0 全程同步阻塞：一条龙跑完出片。无任务队列、无异步轮询。原因：
- v0 目标是验证编排，不是并发吞吐
- 单条流水线串行最易调试
- 异步任务制留到 v4 营销号批量场景

---

## 3. 目录结构

尊重现有骨架，融入 opencode agent 配置：

```
vidance/
├── opencode.json              # opencode 配置：provider + agents + permissions
├── README.md                  # 项目说明（已有，后续更新）
├── AGENTS.md                  # 项目约定 + 命令（供 agent 读）
│
├── .opencode/                 # opencode 原生配置目录
│   ├── agents/
│   │   ├── director.md        # director agent 系统提示词 + 工具权限
│   │   └── reviewer.md        # reviewer subagent 系统提示词
│   └── skills/
│       ├── scriptwriting.md   # 编剧 skill（概念→分镜规范）
│       └── review.md          # 审片 skill（打分标准 + 反馈格式）
│
├── config/
│   └── config.json            # 运行时配置：API key、引擎地址、模型名
│
├── core/                      # 编排层（混合模式的硬编码骨架）
│   └── pipeline.py            # 流水线主控：串联各阶段，调 utils + agent 决策点
│
├── utils/                     # 工具层（纯 code，被 agent 和 core 调用）
│   ├── comfy_api.py           # ComfyUI HTTP 客户端（提交/轮询/取视频）
│   ├── llm.py                 # USTC LLM 调用（文本 + 多模态）
│   ├── tts.py                 # CosyVoice 2 TTS 客户端（旁白→配音+时间戳）
│   ├── ffmpeg_tools.py        # 后处理（抽帧/拼接/配音合成/字幕/转场）
│   └── workflows/
│       └── wan_t2v.json       # Wan T2V workflow 模板（参数占位）
│
├── docs/
│   ├── roadmap.md             # 总览与 v0-v4 路线图
│   ├── v0-design.md           # 本文档
│   ├── hierachy.md            # （已有，空）
│   ├── tech.md                # （已有，空）
│   └── experience.md          # （已有，空）
│
├── input/                     # 输入资源（参考帧等，v0 暂不用）
└── output/                    # 成片 + 元数据（建议软链到 /mnt/dataset）
    ├── clips/                 # 各镜头片段 shot_{id}.mp4 + 抽帧
    └── {task_id}/             # 每次任务的元数据 + 最终成片
```

### 3.1 output 存储策略

机械盘容量大，成片和中间产物放 `/mnt/dataset/zxy/vidance/output/`，`vidance/output` 软链过去：
```
ln -s /mnt/dataset/zxy/vidance/output /mnt/disk_sdb/zxy/vidance/output
```

---

## 4. 端到端流水线

### 4.1 流程图

```
用户: concept="一只猫在月球上跳舞"
  │
  ▼
[1] director: 编剧 ── LLM(deepseek-v4-flash) ──→ script JSON
  │     输入: concept
  │     输出: {title, concept, style, shots:[{id, scene_desc, narration, duration, camera}]}
  │                                            （narration = 该镜中文旁白文本）
  │
  ▼
[2] director: prompt 优化 ── LLM ──→ 每镜 video_prompt
  │     输入: shots[i].scene_desc + script.style
  │     输出: shots[i].video_prompt（英文，Wan 风格描述）
  │
  ▼
[3] core/pipeline: 遍历 shots 生成画面 + 配音
  │   ┌──────────────────────────────────────────────────────┐
  │   │ for each shot:                                       │
  │   │   comfy_api.generate_t2v(                            │
  │   │     prompt=video_prompt,                             │
  │   │     seed=random,  # 随机防缓存命中                    │
  │   │     width=1280, height=704,                          │
  │   │     length=duration*24, steps=20,                    │
  │   │     cfg=5.0)                                         │
  │   │   → output/clips/shot_{id}.mp4                       │
  │   │                                                      │
  │   │   tts.synthesize(                                    │
  │   │     text=narration,  # 中文旁白                       │
  │   │     voice=默认音色)                                   │
  │   │   → output/clips/shot_{id}.wav + timestamp.json      │
  │   │     （配音时长可能 > 画面时长，后处理时对齐）           │
  │   │                                                      │
  │   │   ffmpeg_tools.extract_frames(video, n=4)            │
  │   │   → shot_{id}_frame_{0-3}.jpg                        │
  │   └──────────────────────────────────────────────────────┘
  │
  ▼
[4] director → task → reviewer: 审片
  │     输入: 4 帧图(base64) + scene_desc
  │     reviewer 调 LLM(sonnet-4-6, 多模态)
  │     输出: {score: 1-10, feedback: str, pass: bool}
  │
  ▼
[5] director: 决策
  │     pass=true  → 保留该镜，进入下一镜
  │     pass=false → feedback 传回步骤2，调整 prompt + 新 seed 重做
  │                  最多重试 2 次，仍不过则取最高分版本
  │
  ▼
[6] core/pipeline: 后处理合成
  │     ffmpeg_tools.compose(
  │       clips=[shot_1.mp4, ...],         # 画面片段
  │       audios=[shot_1.wav, ...],        # 配音片段
  │       timestamps=[...],                # TTS 时间戳 → 字幕
  │       narrations=[...],                # 旁白文本 → 字幕内容
  │       transition="crossfade")          # 转场
  │     → 拼接画面 + 合并配音 + 烧录字幕 + 转场
  │     → final.mp4
  │     （画面/配音时长不一致时，按配音时长为准，画面不足则定格末帧或循环）
  │
  ▼
[7] core/pipeline: 存元数据
      → output/{task_id}/meta.json
      → output/{task_id}/final.mp4
```

### 4.2 决策点详解

**决策点 A — 编剧（步骤 1）**
- director 用 `scriptwriting` skill 规范分镜结构
- LLM 输出严格 JSON（用 system prompt 约束格式）
- 镜头数 2-5，每镜 3-6 秒（受 Wan 单次生成上限约束：121 帧≈5s）
- 每镜产出 `narration`（中文旁白文本），用于 TTS 配音；旁白字数与时长匹配（中文约 4 字/秒，5 秒≈20 字）

**决策点 B — prompt 优化（步骤 2）**
- 中文 scene_desc → 英文 video_prompt
- 加入风格修饰词（cinematic / film grain / lighting 等，来自 script.style）
- 避免过长（Wan 对长 prompt 敏感，建议 < 80 词）

**决策点 C — 审片（步骤 4）**
- reviewer 用 `review` skill 的打分标准
- 4 维度打分：与 scene_desc 一致性、画面质量、运动自然度、无明显伪影
- 综合 1-10 分，≥7 通过
- 输出结构化 JSON + 文字反馈

**决策点 D — 重做（步骤 5）**
- director 读 reviewer 的 feedback
- 决策：调整 prompt（LLM 优化）还是仅换 seed 重抽
- 调整策略：feedback 指出具体问题 → 针对性改 prompt；否则只换 seed
- 上限 2 次重试，避免死循环

---

## 5. 数据模型

### 5.1 分镜脚本 schema

```json
{
  "title": "月球上的猫",
  "concept": "一只猫在月球上跳舞",
  "style": "cinematic, warm sunset tone, 35mm film grain",
  "shots": [
    {
      "id": 1,
      "scene_desc": "远景：荒凉的月球表面，一只穿着宇航服的白猫缓缓走入画面",
      "narration": "在寂静的月球上，一只小白猫穿着宇航服，缓缓走入了画面",
      "video_prompt": "Wide shot, a white cat wearing a tiny spacesuit walking slowly into frame across the desolate lunar surface, Earth visible in the black sky, cinematic, 35mm film grain, warm sunset lighting, slow pacing",
      "duration": 5,
      "camera": "wide shot, static camera",
      "params": {
        "width": 1280,
        "height": 704,
        "length": 121,
        "steps": 20,
        "cfg": 5.0,
        "seed": 1234567890
      }
    }
  ]
}
```

字段说明：
- `scene_desc`：中文场景描述（人类可读，审片对照基准）
- `narration`：中文旁白文本（喂给 CosyVoice 做配音，字数与 duration 匹配）
- `video_prompt`：英文生成提示词（实际喂给 Wan）
- `duration`：秒，`length = duration × 24`（帧率）
- `params.seed`：每镜随机生成，记录用于复现

### 5.2 审片结果 schema

```json
{
  "shot_id": 1,
  "attempt": 1,
  "frames": ["shot_1_frame_0.jpg", "shot_1_frame_1.jpg", "shot_1_frame_2.jpg", "shot_1_frame_3.jpg"],
  "score": 8,
  "dimensions": {
    "consistency": 8,
    "quality": 9,
    "motion": 7,
    "artifact": 8
  },
  "feedback": "画面与描述一致，月球场景还原好；猫的运动稍显僵硬，第二帧姿态不自然",
  "pass": true
}
```

### 5.3 任务元数据 schema

```json
{
  "task_id": "20260906_001",
  "concept": "一只猫在月球上跳舞",
  "created_at": "2026-09-06T14:30:00",
  "status": "completed",
  "script": { /* 5.1 完整脚本 */ },
  "clips": [
    {
      "shot_id": 1,
      "attempts": [
        {"attempt": 1, "seed": 123, "video": "clips/shot_1.mp4", "review": { /* 5.2 */ }},
        {"attempt": 2, "seed": 456, "video": "clips/shot_1_v2.mp4", "review": { /* 5.2 */ }}
      ],
      "final_video": "clips/shot_1_v2.mp4",
      "final_score": 8,
      "audio": {
        "narration": "在寂静的月球上，一只小白猫穿着宇航服，缓缓走入了画面",
        "audio_file": "clips/shot_1.wav",
        "duration": 4.8,
        "timestamps": [{"text": "在寂静的月球上", "start": 0.0, "end": 1.2}, ...]
      }
    }
  ],
  "output": "20260906_001/final.mp4",
  "duration_total": 15,
  "total_attempts": 6
}
```

---

## 6. 引擎接口规格

### 6.1 Wan T2V（ComfyUI 8189）

直接调 ComfyUI HTTP API（复用现有 run_wan.py 逻辑，封装到 utils/comfy_api.py）：

```
POST http://127.0.0.1:8189/prompt
  body: {"prompt": <workflow_json>}
  resp: {"prompt_id": "xxx"}

GET http://127.0.0.1:8189/history/{prompt_id}
  resp: {prompt_id: {outputs: {<node_id>: {videos: [{filename, subfolder, ...}]}}}}

GET http://127.0.0.1:8189/view?filename=...&subfolder=...&type=output
  resp: <视频文件二进制>
```

**workflow 节点拓扑**（wan_t2v.json 模板，11 节点）：

| 节点 | class_type | 作用 | 参数注入点 |
|------|-----------|------|-----------|
| 1 | UNETLoader | 加载 Wan2.2 5B 模型 | — |
| 2 | CLIPLoader | 加载 umt5_xxl 编码器 | — |
| 3 | VAELoader | 加载 Wan VAE | — |
| 4 | ModelSamplingSD3 | 采样偏移 | — |
| 5 | CLIPTextEncode | 正向 prompt | **text** ← video_prompt |
| 6 | CLIPTextEncode | 负向 prompt | **text** ← negative_prompt |
| 7 | Wan22ImageToVideoLatent | 视频 latent | **width, height, length** |
| 8 | KSampler | 采样 | **seed, steps, cfg** |
| 9 | VAEDecode | 解码 | — |
| 10 | CreateVideo | 组帧 | **fps** |
| 11 | SaveVideo | 保存 | **filename_prefix** |

模板参数占位用 `{{var}}`，Python 读取后注入。

### 6.2 LLM（USTC）

```
POST https://api.llm.ustc.edu.cn/v1/chat/completions
  headers: Authorization: Bearer sk-M4b0y-Euavlan2tL6ZcWfA
  body: {
    "model": "deepseek-v4-flash",       # 文本：编剧/prompt优化
           或 "claude-sonnet-4-6",      # 多模态：审片
    "messages": [...],
    "temperature": 0.7
  }
```

**多模态审片 messages 格式**：
```json
[
  {"role": "system", "content": "<审片标准 + 输出JSON格式要求>"},
  {"role": "user", "content": [
    {"type": "text", "text": "场景描述：...请按4维度打分"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]}
]
```

模型用途分配：
| 模型 | 用途 | 特点 |
|------|------|------|
| deepseek-v4-flash | 编剧、prompt 优化 | 快、免费、中文好 |
| claude-sonnet-4-6 | 审片（多模态读图） | 推理强、读图准 |
| claude-haiku-4-5 | 备选快速审片 | 更快，精度略低 |

### 6.3 ffmpeg + moviepy（后处理）

v0 需要的后处理功能：
- `extract_frames`：`ffmpeg -i input.mp4 -vf "select='eq(n\,0)+eq(n\,30)+...'" -vsync v0 frame_%d.jpg`（抽指定帧供审片）
- `compose`：核心合成——拼接画面 + 合并配音 + 烧录字幕 + 转场
  - 画面拼接：同编码用 concat demuxer，不同编码重编码
  - 配音合并：`ffmpeg -i video -i audio -c:v copy -c:a aac -shortest`（画面/配音时长不一致时按配音对齐，画面定格末帧）
  - 字幕烧录：从 TTS 时间戳 + 旁白文本生成 .srt，`ffmpeg -i input.mp4 -vf subtitles=sub.srt output.mp4`
  - 转场：moviepy 的 crossfade/fadein/fadeout（镜头间 0.3-0.5s 软过渡）

### 6.4 CosyVoice 2（TTS，FastAPI 9880）

封装为 FastAPI 服务（GPU2），供 `utils/tts.py` 调用：

```
POST http://127.0.0.1:9880/tts
  body: {
    "text": "在寂静的月球上，一只小白猫穿着宇航服",
    "voice": "默认音色",        # v0 用预置音色；v4 支持克隆
    "speed": 1.0
  }
  resp: {
    "audio": "<base64 wav>",
    "duration": 4.8,
    "timestamps": [{"text": "...", "start": 0.0, "end": 1.2}, ...]  # 词/句级时间戳
  }
```

**时间戳决策**（实测后定）：
- 若 CosyVoice 返回词/句级时间戳 → 直接生成 .srt 字幕，**v0 不引入 STT**
- 若时间戳精度不足 → v2 引入 faster-whisper 对齐字幕

**音色**：v0 用 CosyVoice 预置 SFT 音色（固定旁白），zero-shot 克隆留 v4。
**独立 conda env**：CosyVoice 依赖 torch 2.3.1+cu121，与主环境 torch 2.13+cu130 隔离，避免冲突。

---

## 7. 审查闭环

### 7.1 打分标准（review skill）

4 维度，每维 1-10：

| 维度 | 标准 | 低分表现 |
|------|------|---------|
| consistency（一致性） | 画面内容与 scene_desc 匹配度 | 场景/主体/动作与描述不符 |
| quality（画质） | 清晰度、色彩、构图 | 模糊、过曝、灰暗、构图差 |
| motion（运动） | 运动自然度、流畅度 | 静止、卡顿、运动僵硬 |
| artifact（伪影） | 无明显 AI 生成痕迹 | 畸形肢体、融合、闪烁、伪影 |

综合分 = 四维均值。**≥7 通过**。

### 7.2 重试策略

```
attempt 1: 原始 prompt, 随机 seed → 审片
  └ pass → 保留
  └ fail → attempt 2

attempt 2: LLM 根据 feedback 优化 prompt, 新随机 seed → 审片
  └ pass → 保留
  └ fail → attempt 3

attempt 3: 再次优化 prompt 或回退原 prompt, 新 seed → 审片
  └ pass → 保留
  └ fail → 取 3 次中最高分版本（标记 best_effort）
```

- 最多 3 次尝试（1 次原始 + 2 次重试）
- 避免死循环：硬上限
- 最高分兜底：保证流水线不卡死

### 7.3 reviewer subagent 接口

director 通过 opencode `task` tool 派发：
```
task(prompt="审片: 场景描述='...', 关键帧路径=[...], 输出JSON{score,feedback,pass}",
     subagent_type="reviewer")
```
reviewer 返回结构化 JSON，director 解析后决策。

---

## 8. opencode 配置设计

### 8.1 opencode.json

```jsonc
{
  "provider": {
    "ustc": {
      "type": "openai",
      "baseURL": "https://api.llm.ustc.edu.cn/v1",
      "apiKey": "{USTC_API_KEY}"  // 从 config/config.json 或环境变量读
    }
  },
  "agent": {
    "director": {
      "model": "ustc/deepseek-v4-flash",
      "systemPrompt": "...",  // 指向 .opencode/agents/director.md
      "tools": ["bash", "read", "write", "edit", "task", "glob", "grep"]
    },
    "reviewer": {
      "model": "ustc/claude-sonnet-4-6",
      "systemPrompt": "...",  // 指向 .opencode/agents/reviewer.md
      "tools": ["read", "bash"]  // 只读帧图 + 调 LLM
    }
  }
}
```

### 8.2 director agent 职责

- 接收用户 concept
- 调 `llm.chat()` 生成分镜脚本（JSON，含 narration 旁白）
- 调 `llm.chat()` 优化每镜 prompt
- 调 `bash` 运行 `python utils/comfy_api.py generate ...` 生成视频
- 调 `bash` 运行 `python utils/tts.py synthesize ...` 生成配音
- 调 `bash` 运行 `python utils/ffmpeg_tools.py extract ...` 抽帧
- 调 `task` 派 reviewer 审片
- 根据审片结果决策重做或推进
- 调 `bash` 运行 `python utils/ffmpeg_tools.py compose ...` 合成成片
- 返回成片路径

### 8.3 reviewer subagent 职责

- 接收 scene_desc + 关键帧路径
- 读取帧图（base64 编码）
- 调 `llm.chat(model=sonnet-4-6, multimodal)` 打分
- 返回 {score, dimensions, feedback, pass}
- **不修改任何文件，不调用生成工具**

### 8.4 skills

- `scriptwriting.md`：分镜规范（镜头数/时长/JSON 格式/风格描述词库）
- `review.md`：审片标准（4 维度 + 打分锚点 + JSON 输出格式）

---

## 9. 依赖与前置

### 9.1 系统依赖

| 依赖 | 用途 | 安装方式 | 状态 |
|------|------|---------|------|
| ffmpeg | 抽帧/拼接/字幕/配音合成 | `conda install -c conda-forge ffmpeg` | 🔄 安装中 |
| sox | CosyVoice 音频处理依赖 | `conda install -c conda-forge sox` | 🔄 安装中 |
| Python 3.10 (cosyvoice env) | CosyVoice 运行时（torch2.3.1+cu121） | `conda create -n cosyvoice python=3.10` | ✅ |
| Python 3.11+ (主环境) | vidance 编排运行时 | 已有 | ✅ |
| ComfyUI (Wan 8189) | T2V 引擎 | 已部署 | ✅ |

### 9.2 Python 依赖

主环境（vidance 编排）：

| 包 | 用途 | 状态 |
|----|------|------|
| urllib (stdlib) | HTTP 调用 | ✅ |
| json (stdlib) | 数据序列化 | ✅ |
| PIL/Pillow | 图像 base64 编码 | ✅ 12.2.0 |
| subprocess (stdlib) | 调 ffmpeg | ✅ |
| moviepy | 视频编辑（字幕/转场高级封装） | ❌ 待装 |

cosyvoice env（独立，TTS 引擎）：

| 包 | 用途 | 状态 |
|----|------|------|
| torch 2.3.1+cu121 | CosyVoice 推理 | 🔄 安装中 |
| cosyvoice | TTS 模型 | 🔄 安装中 |
| modelscope | 模型下载 | 🔄 安装中 |
| fastapi/uvicorn | TTS 服务 | 🔄 安装中 |

> 不需要爬虫库（bs4/yt-dlp 等）—— 那是 v4 营销号的需求。

### 9.3 模型依赖

v0 需要的模型：

| 模型 | 用途 | 位置 | 状态 |
|------|------|------|------|
| Wan2.2-5B | T2V 主力 | GPU2:8189 | ✅ 已部署 |
| CosyVoice2-0.5B | TTS 配音 | GPU2:9880, /mnt/dataset/zxy/CosyVoice2-0.5B/ | 🔄 部署中 |

无需 TripoSplat/RIFE（v1+ 才需要）。

### 9.4 API 依赖

- USTC LLM API key：`sk-M4b0y-Euavlan2tL6ZcWfA`（存 config/config.json，不入 git）
- 模型：deepseek-v4-flash + claude-sonnet-4-6（均已验证可用）

---

## 10. 验收标准

v0 跑通的标志：

1. **端到端**：输入一句中文概念 → 输出一个有声 mp4 文件，无需人工干预
2. **多镜头**：成片含 2-5 个镜头，总时长 10-30 秒
3. **有声**：含 CosyVoice 配音 + 字幕（基于 TTS 时间戳）
4. **审片闭环**：每镜有审片记录，不通过的有重试记录
5. **元数据**：output/{task_id}/meta.json 完整记录脚本/prompt/参数/配音/审片结果
6. **可复现**：meta.json 中的 seed 和 params 可复跑出相同结果
7. **agent 可控**：director 的每步决策可追溯（日志/元数据）

验收命令（设计）：
```bash
# 启动后，在 opencode 中对 director 说：
> 生成一个短片：一只猫在月球上跳舞
# 预期：director 自主编排全流程，最终返回成片路径
```

---

## 11. 后续演进方向（简述）

| 版本 | 核心新增 | 关键依赖 |
|------|---------|---------|
| v1 | 角色一致性：FLUX 生角色→TripoSplat 出 3DGS→RenderSplat 渲染各角度参考帧→Wan I2V | TripoSplat 模型、RenderSplat |
| v2 | 长视频：RIFE 光流插帧软过渡 + ffmpeg 调色/配乐 | RIFE 模型、ffmpeg 滤镜 |
| v3 | 2D→3D→新视角：Hunyuan3D mesh 重建 + headless 渲染器 | Hunyuan3D 模型、mesh 渲染方案（Isaac Sim/Blender/trimesh 待定） |
| v4 | 营销号流水线：爬热点→选题→批量生成→发布 + 异步任务制 | 爬虫库、任务队列 |

> v1 的 mesh headless 渲染是已知阻塞点，需在 v1 启动前确定渲染方案。

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Wan 单次生成上限 ~5s（121帧） | 长镜头无法一次生成 | v0 限制每镜 ≤5s，v2 用插帧延长 |
| 画面/配音时长不一致 | 成片不同步 | 后处理按配音时长对齐，画面不足定格末帧或循环 |
| CosyVoice 时间戳精度不足 | 字幕对不齐 | 实测评估；不够则 v2 引入 faster-whisper |
| ComfyUI 缓存命中（同 prompt+seed 秒出旧结果） | 重试无效 | 每次生成强制随机 seed |
| LLM 输出 JSON 格式不稳定 | 脚本解析失败 | system prompt 严格约束 + JSON 修复重试 |
| sonnet-4-6 审片评分主观偏差 | 误判通过/不通过 | 4 维度锚点 + 阈值可调 + 最高分兜底 |
| CosyVoice 独立 env 依赖冲突 | TTS 服务起不来 | 隔离 conda env，torch 2.3.1+cu121 独立 |
| Wan 8189 服务掉线 | 生成失败 | comfy_api 加健康检查 + 重试 |
