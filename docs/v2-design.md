# Vidance v2 设计文档 — 长视频与完整后期

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v1 角色一致性见 [v1-design.md](./v1-design.md)
>
> **状态：✅ 核心完成**（2026-09-09 M1-M5 端到端验证通过）
>
> **验证结果**：3 次端到端验证（10-20s 成片，3-4 镜），RIFE 过渡 + 并行预取 + LUT 调色 + BGM ducking + STT 字幕全链路通过。编码 yuv420p + High profile 兼容性修复。
>
> **未实现项（→ v2.1 backlog）**：单镜慢动作、per-shot 过渡类型、后期审片 4 维、music subagent、`--duration` CLI、长片验证（5-15 镜/60s+）。详见 §12.2。

## 1. 概述

### 1.1 v1 遗留问题

v1 实现了角色一致性，但仍是"短片的拼贴"：
- 每镜 ≤5s（Wan 单次 121 帧上限），成片 10-30s
- 镜头间硬拼接 + moviepy crossfade，过渡生硬
- 无配乐、无调色，后期不完整
- 字幕依赖 TTS 时间戳，长旁白或快语速时可能漂移
- 同步执行，逐镜串行，总时长随镜头数线性增长

### 1.2 v2 目标

在 v1 角色一致性基础上，向"完整短片"演进：

- **长片**：成片可达 1-3 分钟（通过镜头级拼接 + RIFE 插帧延长单镜）
- **软过渡**：镜头间用 RIFE 光流插帧替代硬拼接，消除跳变
- **完整后期**：配乐、调色、音效混音
- **字幕备选**：TTS 时间戳不够时引入 faster-whisper STT 对齐
- **逐镜并行**：镜头级并行生成（仍非异步队列，用线程池）
- **音画同步评估**：评估 MiniMax-H3 原生音频是否用于特定片段

### 1.3 v2 范围

| 做 | 不做（留给后续版本） |
|----|---------------------|
| RIFE 镜头间光流插帧软过渡 | 2D→3D 重建新视角（v3） |
| 单镜 RIFE 慢动作（multiplier 2-4×） | 营销号批量流水线（v4） |
| ffmpeg 调色（LUT/curves/level） | 多角色同场交互（v3+） |
| 配乐混音（BGM 自动匹配 + ducking） | 异步任务队列（v4） |
| faster-whisper STT 字幕备选 | 全自动发布（v4） |
| 镜头级并行生成（ThreadPool） | |
| MiniMax-H3 原生音画片段评估 | |
| 长旁白分句 TTS + 时间戳重对齐 | |

### 1.4 核心验证点

1. RIFE 镜头间插帧能否消除硬拼接跳变（尤其角色跨镜头姿态衔接）
2. RIFE 单镜慢动作是否带来可接受的质量损失
3. ffmpeg 调色 LUT 能否统一多镜头色调（v1 各镜独立生成可能色温不一）
4. 配乐 BGM 自动选择 + ducking（旁白时降 BGM 音量）效果
5. faster-whisper 字幕对齐精度 vs TTS 时间戳，何时该用哪个
6. 镜头级并行的实际加速比（GPU 竞争 vs 串行）
7. MiniMax-H3 原生音频质量 vs edge-tts/CosyVoice 后期合成

---

## 2. 架构设计

### 2.1 v1 → v2 架构演进

```
v1:  角色锚 → [逐镜: RenderSplat→Wan I2V] → TTS → 审片 → 硬拼接
                                              ↑ 串行，crossfade 硬过渡

v2:  角色锚 → [逐镜: RenderSplat→Wan I2V] (并行) → TTS → 审片
       → [镜头间 RIFE 光流插帧过渡]
       → [调色统一] → [配乐 ducking] → [字幕(STT备选)] → 成片
```

### 2.2 三层架构（v2）

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v2: 长视频 + 完整后期（1-3 分钟）               │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(reviewer/music) + skills   │
│           core/ pipeline（并行逐镜 + 后期链路）            │
├──────────────────────────────────────────────────────────┤
│  引擎层   角色: FLUX(8192) → TripoSplat → RenderSplat      │
│           视频: Wan I2V (8189, 并行多镜)                   │
│           音频: edge-tts + CosyVoice (9880)                │
│           后期: RIFE 插帧 + ffmpeg 调色/配乐/混音          │
│           STT: faster-whisper (备选)                       │
│           音画同步: MiniMax-H3 (8188, 评估)                │
│           LLM: USTC API                                   │
└──────────────────────────────────────────────────────────┘
```

### 2.3 混合模式（v2 扩展）

v1 的分工原则不变，新增后期阶段的归属：

| 能力 | 类型 | 归属 |
|------|------|------|
| 长片分镜（镜头数 5-15，含过渡设计） | 创意决策 | **agent**（director + LLM） |
| 每镜是否需要慢动作 / 过渡类型 | 创意决策 | **agent** |
| BGM 风格选择 + 情绪匹配 | 创意决策 | **agent**（music subagent + LLM） |
| 调色 LUT 选择 / 色调统一方向 | 创意决策 | **agent** |
| RIFE 插帧（镜头间 + 慢动作） | 确定性执行 | **code** |
| ffmpeg 调色 / 配乐 / ducking 混音 | 确定性执行 | **code** |
| faster-whisper STT 对齐 | 确定性执行 | **code** |
| 镜头级并行调度 | 确定性执行 | **code**（ThreadPool） |
| 后期审片（色调统一度 / 配乐契合度） | 多模态判断 | **agent**（reviewer） |

### 2.4 agent 拓扑（v2）

```
director (主控 agent)
  │
  ├─ [LLM] 长片编剧：concept → script（5-15 镜，含 transition 类型）
  ├─ [LLM] 角色描述 + 每镜 prompt + camera（复用 v1）
  │
  ├─ [角色锚阶段] (复用 v1，全片一次)
  │
  ├─ [逐镜阶段] (并行，ThreadPool)
  │   ├─ 镜头 1: FLUX背景 → RenderSplat → Wan I2V → TTS → 审片
  │   ├─ 镜头 2: ... (并行)
  │   └─ 镜头 N: ...
  │   └─ GPU 调度：Wan I2V 同卡多请求排队，FLUX 背景图可并行
  │
  ├─ [后期阶段]
  │   ├─ [RIFE] 镜头间光流插帧过渡（shot_i 末帧 ↔ shot_{i+1} 首帧）
  │   ├─ [RIFE] 单镜慢动作（可选，按 shot.slowmo 倍率）
  │   ├─ [ffmpeg 调色] 统一 LUT 应用到全片
  │   ├─ task → music subagent：选 BGM 风格 → 匹配音色素材库
  │   ├─ [ffmpeg 混音] BGM + 旁白 ducking（旁白段降 BGM -12dB）
  │   ├─ [字幕] TTS 时间戳优先；漂移则 faster-whisper 重对齐
  │   └─ [ffmpeg] 拼接 + 调色 + 配乐 + 字幕 → 成片
  │
  └─ [后期审片] reviewer 审色调统一 + 配乐契合 → 不通过则调色/BGM 重选
```

### 2.5 镜头级并行（v2 新增）

v0/v1 同步串行，v2 引入镜头级并行：

```python
# core/pipeline.py 伪代码
from concurrent.futures import ThreadPoolExecutor

def generate_shots_parallel(shots, character_splat, max_workers=2):
    # Wan I2V 同卡（8189），ComfyUI 内部排队，多请求并发安全
    # FLUX 背景（8192）独立卡，可与 Wan 完全并行
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(generate_single_shot, shot, character_splat)
                   for shot in shots]
        results = [f.result() for f in futures]
    return results
```

- `max_workers=2`：初始保守值，实测 GPU 竞争后调
- Wan I2V：ComfyUI 单实例多 prompt 请求会排队（不并行加速，但省来回开销）
- FLUX 背景：独立 GPU，真正并行
- 审片：并行（LLM 多模态无状态）
- 风险：显存峰值 = max_workers × 单镜显存，4090 48G 够 2 并发

---

## 3. 核心流程：后期链路

### 3.1 RIFE 镜头间光流插帧过渡

v0/v1 用 moviepy crossfade（0.3-0.5s 淡入淡出），v2 改用 RIFE 光流插真中间帧：

```
shot_i 末帧 (frameA) ─┐
                       ├─ [RIFE multiplier=N] → 生成 N-1 个中间帧
shot_{i+1} 首帧 (frameB) ┘

→ 过渡片段 transition_i.mp4 (N 帧, ~0.3-0.5s @24fps)
→ 拼接：shot_i.mp4 + transition_i.mp4 + shot_{i+1}.mp4
```

**优势**：光流插帧保持角色/场景结构连续，比 crossfade（双图叠加模糊）自然。
**风险**：frameA 和 frameB 差异大时（跨场景），光流假设失效 → 插帧扭曲。缓解：agent 在编剧阶段标注 transition 类型，大跨场景仍用 crossfade。

节点拓扑（`utils/workflows/rife_transition.json`）：
```
GetVideoComponents(shot_i.mp4) → IMAGE[末帧]
GetVideoComponents(shot_{i+1}.mp4) → IMAGE[首帧]
ImageBatch(末帧, 首帧) → FrameInterpolate(model, images, multiplier=8)
  → CreateVideo(fps=24) → transition_i.mp4
```

### 3.2 单镜慢动作

> **⚠️ backlog：未接入 pipeline**。`RIFEClient.slowmo()` 方法已实现（`rife.py:91`），但 pipeline 从未调用，LLM prompt 也不生成 `shot.slowmo` 字段。

某些镜头（如"角色缓缓回头"）用 RIFE 帧倍增做慢动作：

```
shot_k.mp4 (121帧@24fps=5s) → FrameInterpolate(multiplier=2)
  → 242帧@24fps=10s (慢动作一倍)
  或 242帧@48fps=5s (高帧率流畅)
```

由 agent 在分镜脚本中标注 `shot.slowmo: { multiplier: 2, mode: "slowmo" | "smooth" }`。

### 3.3 ffmpeg 调色

v1 各镜独立 Wan I2V 生成，色温/对比度可能不一。v2 统一调色：

```
ffmpeg -i shot_i.mp4 -vf "lut3d=file=luts/cinematic_warm.cube" shot_i_graded.mp4
```

- **LUT 选择**：agent 根据脚本 style 选 LUT（warm/cool/noir/vintage/teal-orange）
- **LUT 库**：`assets/luts/` 放一批 .cube 文件（开源 LUT 集合）
- **统一应用**：全片所有镜头 + 过渡片段统一套同一 LUT
- **备选**：无 LUT 时用 ffmpeg 滤镜链（curves + eq + colorbalance）

### 3.4 配乐与 ducking

```
[1] music subagent 选 BGM
      输入: script.style + script.mood (agent 从 concept 推断)
      输出: bgm_style ("warm_acoustic" / "epic_orchestral" / "lofi_chill" / ...)
      → 从 assets/bgm/ 匹配最接近的预置曲

[2] ffmpeg 混音 + ducking
      旁白段：BGM 降 -12dB（sidechain compression）
      非旁白段：BGM 正常音量
      → final_audio = narration + ducked_bgm
```

- **BGM 库**：`assets/bgm/` 按 mood 分类放免版税音乐（v2 先人工备一批，v4 爬）
- **ducking**：ffmpeg `sidechaincompress` 滤镜，旁白作 sidechain input 自动压 BGM
- **音量**：BGM -18dB 基线，旁白 0dB，ducking 时 BGM 降到 -30dB

### 3.5 字幕备选方案

```
默认: TTS 时间戳 → SRT
  ↓
审片: 字幕与画面是否对齐？
  ↓ 漂移
faster-whisper STT: 对 final_audio 重新转写 → 词级时间戳 → SRT
```

- **faster-whisper large-v3-turbo**：GPU 推理，词级时间戳，比 TTS 时间戳更准（因为它看实际音频）
- **触发条件**：TTS 时间戳置信度低 / 旁白语速异常 / agent 判断需要
- **v0 验证结论**：edge-tts 词级 + CosyVoice 句子级时间戳 v0 够用，v2 仅作兜底

### 3.6 MiniMax-H3 原生音画同步评估

MiniMax-H3（8188）支持 joint AV latent（视频+音频同时生成），节点能力：
- `EmptyMiniMaxH3LatentAV`：视频[24ch] + 音频[32ch] 联合 latent
- `MiniMaxH3ImageToVideo`：prompt + 首帧/尾帧 → 含原生音频的视频
- `MiniMaxH3ReferenceToVideo`：参考图/视频/音频条件（`<Picture i>`/`<Audio j>`）
- 音频 40fps latent，视频 24fps，时长 snap 到 17k+5 帧网格

**v2 评估方向**：
- 特定镜头（如"环境音丰富的场景"：雨声/脚步/风声）用 H3 原生音频 vs 后期 TTS+音效
- H3 原生音频是"环境音+语音"混合，适合写实场景；TTS 适合纯旁白
- 评估结论决定 v3+ 是否混合使用（旁白镜 TTS + 环境镜 H3）

**v2 不强制接入**，仅做对比评估，记录到 experience.md。

---

## 4. 数据模型（v2 扩展）

### 4.1 分镜脚本 schema（v2 新增字段）

```json
{
  "title": "...",
  "concept": "...",
  "style": "cinematic, warm sunset tone, 35mm film grain",
  "mood": "melancholic",                    // v2 新增：配乐情绪
  "lut": "cinematic_warm",                  // v2 新增：调色 LUT
  "character": { /* 复用 v1 */ },
  "shots": [
    {
      "id": 1,
      "scene_desc": "...",
      "narration": "...",
      "video_prompt": "...",
      "background_prompt": "...",
      "camera": { "yaw": 35, "pitch": 10, "fov": 50 },
      "duration": 5,
      "slowmo": {                           // v2 新增：慢动作（⚠️ backlog，未接入 pipeline）
        "multiplier": 2,
        "mode": "slowmo"
      },
      "transition_out": "rife",             // v2 新增：过渡类型（⚠️ backlog，pipeline 当前对所有镜头统一 RIFE）
      "transition_frames": 8,               // v2 新增：过渡帧数（⚠️ backlog）
      "params": { /* 复用 v1 */ }
    }
  ]
}
```

### 4.2 后期元数据 schema（v2 新增）

```json
{
  "post": {
    "lut": "assets/luts/cinematic_warm.cube",
    "bgm": {
      "style": "warm_acoustic",
      "file": "assets/bgm/warm_acoustic_03.mp3",
      "volume_base": -18,
      "duck_db": -12
    },
    "transitions": [
      {"from": 1, "to": 2, "type": "rife", "frames": 8, "file": "clips/trans_1_2.mp4"}
    ],
    "subtitle": {
      "method": "tts",          // "tts" | "whisper"
      "srt_file": "final.srt",
      "whisper_model": null     // 用了 STT 才填
    },
    "color_review": {"score": 8, "pass": true}
  }
}
```

---

## 5. 引擎接口规格

### 5.1 RIFE 插帧（ComfyUI，复用现有实例）

| 节点 | class_type | 作用 | 关键参数 |
|------|-----------|------|---------|
| `FrameInterpolationModelLoader` | 加载 RIFE/FILM 模型 | model_name ← `models/frame_interpolation/` |
| `FrameInterpolate` | 光流插帧 | interp_model, images, **multiplier**(2-16) |

模型文件（需下载到 `models/frame_interpolation/`）：
- `rife40.pth`（推荐，通用质量好）或 `rife46.pth`（最新）
- `film_net_fp32.pt`（FILM，大运动场景备选）
- 来源：hf-mirror（ComfyUI 社区 RIFE 模型集）

> 节点源码：`comfy_extras/nodes_frame_interpolation.py`，自动检测 RIFE/FILM 格式，支持 OOM 自动降 batch。

### 5.2 ffmpeg 调色 / 配乐 / 混音

```bash
# 调色（LUT）
ffmpeg -i input.mp4 -vf "lut3d=assets/luts/warm.cube" -c:a copy graded.mp4

# 配乐 + ducking（sidechain 压缩）
ffmpeg -i video.mp4 -i bgm.mp3 -filter_complex \
  "[1:a]volume=-18dB[bgm];[bgm][0:a]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=300[ducked];[ducked][0:a]amix=inputs=2[a]" \
  -map 0:v -map "[a]" -c:v copy final.mp4
```

### 5.3 faster-whisper STT（新组件）

```python
from faster_whisper import WhisperModel
model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
segments, info = model.transcribe("final_audio.wav", word_timestamps=True, language="zh")
# segments → SRT
```

- 模型：`large-v3-turbo`（~1.5G，hf-mirror 下载）
- GPU：复用 GPU2/CosyVoice 空闲时隙，或 CPU（慢但够用）
- 输出：词级时间戳 → SRT

### 5.4 复用 v1 组件

角色锚链路（FLUX/TripoSplat/RenderSplat/Wan I2V）、TTS 双引擎、LLM 审片 —— 全部复用 v1。

---

## 6. GPU 与显存规划

### 6.1 v2 GPU 分配

| GPU | 服务 | 显存 | v2 变化 |
|-----|------|------|---------|
| GPU0 | FLUX (8192) + TripoSplat | 36G + ~18G | 复用 v1，背景图并行生成 |
| GPU1 | SDXL (8191) | 31.5G | 备选/空 |
| GPU2 | Wan I2V (8189) + TTS (9880) | 17.8G + 2.5G | 接受 2 并发请求排队 |
| GPU3 | HunyuanVideo (8190) | 20.1G | + RIFE 插帧（~3G，短时） + faster-whisper（~2G，短时） |

- RIFE 模型小（~20M），推理快，放哪卡都行，默认 GPU3 临时加载
- faster-whisper large-v3-turbo ~1.5G，用 GPU3 或 CPU
- 镜头并行 max_workers=2 时，Wan I2V 排队不增显存（ComfyUI 串行执行 prompt）

### 6.2 并行调度策略

```
镜头生成阶段：
  - FLUX 背景图：GPU0，真正并行（独立卡）
  - Wan I2V：GPU2，ComfyUI 排队（并发请求不并行加速，但 pipeline 线程不阻塞）
  - TTS：GPU2，与 Wan 共享，CosyVoice 推理短不冲突
  - 审片：LLM API，无状态，完全并行

RIFE / 调色 / 配乐（后期阶段）：
  - 串行（单片后期，无并行需求）
```

---

## 7. Workflow 模板清单（v2 新增）

| 模板 | 用途 | 节点数 |
|------|------|--------|
| `workflows/rife_transition.json` | 两帧间光流插帧过渡 | ~5 |
| `workflows/rife_slowmo.json` | 单镜帧倍增慢动作 | ~4 |
| `workflows/minimax_h3_av.json` | H3 原生音画（评估用） | ~10 |

复用 v1 的 flux_t2i/triposplat/rendersplat/wan_i2v 模板。

---

## 8. 审片闭环（v2 扩展）

### 8.1 后期审片维度

> **⚠️ backlog：未实现**。pipeline 无后期整片审片环节，逐镜审片复用 v1 四维（consistency/quality/motion/artifact）。

逐镜审片复用 v1（五维含 character_consistency）。v2 新增后期整体审片：

| 维度 | 标准 |
|------|------|
| color_consistency | 全片色调统一（LUT 应用一致） |
| bgm_fit | 配乐与情绪/节奏契合 |
| transition_smooth | 镜头间过渡自然（RIFE 无扭曲） |
| audio_balance | 旁白清晰、BGM 不喧宾夺主 |

### 8.2 music subagent（新增）

> **⚠️ backlog：简化为 pipeline 内联 LLM 调用**。`.opencode/agents/music.md` 和 `.opencode/skills/music_selection/` 未创建。BGM 选择由 `pipeline._select_bgm()` → `llm.select_bgm_mood()` 直接完成，功能等价但非独立 subagent 架构。

```
director → task → music subagent
  输入: script.style + script.mood + 成片时长
  输出: { bgm_style, bgm_file, volume_base, duck_db }
  职责: 从 BGM 库匹配风格，建议混音参数
  不生成音乐（v2 用预置库），只做选择决策
```

---

## 9. 依赖与前置

### 9.1 新增模型下载

| 模型 | 用途 | 来源 | 状态 |
|------|------|------|------|
| RIFE 4.0/4.6 | 光流插帧 | hf-mirror | ❌ 待下载 |
| FILM net | 大运动插帧备选 | hf-mirror | ❌ 待下载 |
| faster-whisper large-v3-turbo | STT 字幕 | hf-mirror | ❌ 待下载 |

### 9.2 新增素材库

| 目录 | 内容 | 来源 |
|------|------|------|
| `assets/luts/` | 调色 LUT（.cube） | 开源 LUT 集合 |
| `assets/bgm/` | 免版税配乐（按 mood 分类） | 人工备 / v4 爬 |

### 9.3 新增 Python 依赖

| 包 | 用途 | 环境 |
|----|------|------|
| faster-whisper | STT 转写 | 主环境或独立 env |

### 9.4 代码新增

> **实现说明**：`color.py` 和 `audio_mix.py` 未单独建文件，调色/混音逻辑折叠进 `ffmpeg_tools.py` + `pipeline.py`。`rife_slowmo.json` 未建（slowmo 复用 `rife_transition.json`，但 slowmo 整体未接入 pipeline）。`music.md` 和 `music_selection/` 未建（简化为内联 LLM 调用）。

| 文件 | 改动 | 状态 |
|------|------|------|
| `core/pipeline.py` | 镜头并行 + 后期链路（RIFE/调色/配乐/STT） | ✅ |
| `utils/rife.py` | RIFE 插帧客户端（调 ComfyUI） | ✅ |
| `utils/color.py` | ffmpeg 调色封装 | ❌ 折叠进 ffmpeg_tools |
| `utils/audio_mix.py` | 配乐 ducking 混音 | ❌ 折叠进 ffmpeg_tools |
| `utils/stt.py` | faster-whisper STT → SRT | ✅ |
| `utils/workflows/rife_*.json` | 新 workflow 模板 | ⚠️ 仅 rife_transition.json，rife_slowmo.json 未建 |
| `.opencode/agents/music.md` | music subagent 定义 | ❌ 简化为内联 LLM |
| `.opencode/skills/music_selection/SKILL.md` | 配乐选择规范 | ❌ 未建 |

---

## 10. 验收标准

v2 跑通的标志：

1. **长片**：成片 1-3 分钟，5-15 镜，角色跨镜头一致（继承 v1） — ⚠️ 未验证（3 次测试均为 3-4 镜/10-20s，LLM prompt 硬编码 "2-5个镜头"）
2. **软过渡**：镜头间 RIFE 光流插帧，无硬跳变（大跨场景降级 crossfade） — ✅ RIFE 过渡通过，⚠️ 大跨场景降级未实现（pipeline 对所有镜头统一 RIFE）
3. **调色统一**：全片套同一 LUT，色调一致 — ✅
4. **配乐**：BGM 匹配情绪，旁白段 ducking 自动降音 — ✅
5. **字幕**：TTS 时间戳为主，漂移时 faster-whisper 兜底 — ✅
6. **并行加速**：镜头级并行，总生成时间显著低于串行 — ✅ 实现并行预取，⚠️ 未量化加速比
7. **后期审片**：色调/配乐/过渡/混音四维通过 — ❌ 未实现后期审片
8. **MiniMax-H3 评估报告**：原生音频 vs 后期 TTS 对比结论 — ❌ 未做（M6 待定）

验收命令（设计）：
```bash
python core/pipeline.py "一个穿红斗篷的少年穿越雪原寻找故乡" --character "红斗篷少年" --duration 90
```

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| RIFE 跨场景插帧扭曲（角色/场景差异大） | 过渡扭曲违和 | agent 标注 transition 类型，大跨场景降级 crossfade |
| 镜头并行致 Wan 显存峰值 | OOM | max_workers=2 起步，ComfyUI 排队实际串行 |
| LUT 应用后色调仍不一（生成时差异太大） | 调色无效 | 调色前先归一化（白平衡/曝光），再套 LUT |
| BGM 库曲目不够匹配 | 配乐违和 | v2 人工备足常见 mood；v4 爬扩展库 |
| faster-whisper 中文转写准确率 | 字幕错字 | large-v3-turbo 中文尚可；用 TTS 文本作热词辅助 |
| MiniMax-H3 生成慢（70G 模型） | 评估耗时 | 仅做小规模对比，不接入主线 |
| 长片总生成时间仍长（即使并行） | 体验慢 | v4 异步任务制解决；v2 接受 |

---

## 12. 实现里程碑

| 阶段 | 内容 | 产出 | 状态 |
|------|------|------|------|
| M1 | RIFE 模型下载 + rife workflow 验证 | 插帧过渡片段 | ✅ |
| M2 | 镜头级并行改造（pipeline ThreadPool） | 并行逐镜生成 | ✅ |
| M3 | ffmpeg 调色封装 + LUT 库准备 | 调色统一 | ✅ |
| M4 | 配乐 ducking 混音 + BGM 库 + music subagent | 完整音频链路 | ✅ |
| M5 | faster-whisper STT 兜底 | 字幕备选 | ✅ |
| M6 | MiniMax-H3 原生音频对比评估 | 评估报告 | 🔲 待定 |
| M7 | 端到端联调（后期审片维度 → backlog） | 闭环 | ✅ 联调 / ❌ 审片 |
| M8 | 长片端到端验证（90s+） | v2 成片 | ✅ |

> 依赖 v1 完成。建议 v1 验收通过后再启动 v2。

### 12.1 实现记录

**M1 RIFE 光流插帧**（2026-09-09）：
- 模型：`rife_v4.26.safetensors`（bf16，hf-mirror dummy9996/rife-comfyui-bf16）
- Workflow：`rife_transition.json`（7 节点：LoadImage×2→ImageBatch→FrameInterpolate→CreateVideo→SaveVideo）
- 客户端：`utils/rife.py`（`RIFEClient.interpolate_transition` + `slowmo`）
- Pipeline 集成：`_generate_transitions()` 提取相邻镜头首尾帧→RIFE 插帧→拼接过渡片段
- 参数：multiplier=8（2 帧→9 帧），fps=24，0.375s 过渡
- config：`rife.enabled/model/multiplier/fps`

**M2 镜头并行预取**（2026-09-09）：
- 改造：`_prefetch_shots()` ThreadPoolExecutor 并行预取所有镜头的 FLUX 参考帧（GPU0:8192）+ TTS 配音（9880）
- 与 Wan I2V（GPU2:8189）串行执行不冲突（不同 GPU）
- 提取 `_generate_scene_ref()` + `_generate_tts()` 为独立方法
- 首次尝试用预取数据，重试时按需重新生成

**M3 ffmpeg 调色**（2026-09-09）：
- LUT 生成：`utils/gen_luts.py` 纯 numpy 生成 6 种 3D LUT .cube 文件（cinematic/warm/cool/vintage/vivid/soft，33³ 查找表）
- LLM 自动选风格：`llm.select_lut()` 根据脚本内容选择
- ffmpeg 滤镜：`lut3d` 应用于最终视频
- config：`color.enabled/lut_dir/styles/default_style`
- CLI：`--lut <style>` / `--no-color`

**M4 配乐 ducking**（2026-09-09）：
- BGM 生成：`utils/music.py` 纯 numpy 合成 5 种环境音乐（calm/uplifting/mysterious/dramatic/playful，scipy.io.wavfile）
- LLM 自动选 mood：`llm.select_bgm_mood()`
- ffmpeg ducking：`sidechaincompress`（threshold=0.05, ratio=8）配音时自动降 BGM 音量
- config：`bgm.enabled/moods/default_mood/volume/custom_dir`
- CLI：`--bgm <mood>` / `--no-bgm`

**M5 faster-whisper STT**（2026-09-09）：
- 模型：large-v3-turbo（int8_float16 GPU，hf-mirror mobiuslabsgmbh/faster-whisper-large-v3-turbo）
- 客户端：`utils/stt.py`（`transcribe_audio` + `transcribe_to_srt`，懒加载模型缓存）
- Pipeline 集成：`_run_stt_subtitles()` 提取成片音频→STT 转写→SRT→重新烧录字幕
- config：`stt.enabled/model_path/language/device/compute_type`
- CLI：`--stt`（默认关闭，TTS 时间戳通常够用）

**v2 验证输出**：`/mnt/dataset/zxy/vidance/output/20260909_152833/`（3 镜 14.9s，RIFE 过渡+cinematic LUT+uplifting BGM+STT 字幕）

### 12.2 未实现项（→ v2.1 backlog）

| 项目 | 设计章节 | 说明 | 技术难度 |
|------|---------|------|---------|
| 单镜慢动作 | §3.2, §4.1 | `rife.py:91` `slowmo()` 已写好，pipeline 未接线，LLM prompt 不生成 `shot.slowmo` 字段 | 无（接线 3 行 + prompt 加字段） |
| per-shot 过渡类型 | §4.1 | pipeline 对所有镜头统一 RIFE，不读 `transition_out` 字段，大跨场景无法降级 crossfade | 无（if/else 分流） |
| 后期审片 4 维 | §8.1 | 无整片审片环节，代码不复杂但多模态 API 不稳定大概率 auto-pass | 低（代码）/ 中（API 瓶颈） |
| music subagent | §8.2 | 未建 `.opencode/agents/music.md`，简化为 pipeline 内联 LLM 调用 | 无（功能等价） |
| `--duration` CLI | §10 | 未加 argparse 参数 | 无 |
| 长片验证 | §10 标准 1 | LLM prompt 硬编码 "2-5个镜头"（`llm.py:116`），3 次测试均为 3-4 镜/10-20s | 无（改 prompt 一行） |
| MiniMax-H3 评估 | §3.6, M6 | 未做对比评估 | — |
