# Vidance 总览与路线图

> 视频生成 agent 系统：opencode 当"制片人"编排本地生图/生视频/生3D/语音引擎，从一句概念产出有声短片。

## 1. 定位

不是 API 服务，是 **"agent 当导演"** 的视频生产系统：

| 角色 | 组件 | 说明 |
|------|------|------|
| 制片人 | opencode（director agent） | 全流程主控，关键决策点介入 |
| 编剧/策划 | LLM（deepseek-v4-flash） | 概念→分镜脚本→prompt 优化 |
| 摄影/演员 | 生图生视频模型 | SDXL / FLUX / Wan / HunyuanVideo / MiniMax-H3 |
| 角色锚 | 3D 重建 | TripoSplat(3DGS) / Hunyuan3D(mesh)，跨镜头不漂移 |
| 配音员 | TTS（CosyVoice 2） | 旁白文本→语音，支持音色克隆 |
| 审片员 | 多模态 LLM（claude-sonnet-4-6） | 逐帧读图打分，闭环重做 |
| 剪辑师 | ffmpeg + moviepy | 拼接/字幕/配乐/调色/转场 |

**核心创新**：3DGS 充当"角色锚"——每镜头起始帧由 RenderSplat 从同一 3DGS 按镜头角度渲染，喂 I2V 生成，避免跨镜头角色漂移。

---

## 2. 三层架构

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v0 有声短片 → v1 角色一致性 → v2 长视频          │
│         v3 2D→3D→新视角 → v4 营销号流水线                  │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(reviewer) + skills         │
│           core/ Python 流水线骨架（混合模式）              │
├──────────────────────────────────────────────────────────┤
│  引擎层   T2I: SDXL / FLUX                                │
│         T2V: Wan2.2 / HunyuanVideo / MiniMax-H3           │
│         3D:  TripoSplat(3DGS) / Hunyuan3D(mesh) / RenderSplat │
│         音频: CosyVoice 2 (TTS) / faster-whisper (STT备选)│
│         后处理: ffmpeg + moviepy + RIFE(插帧)              │
│         LLM: USTC API (deepseek-v4-flash / sonnet-4-6)    │
└──────────────────────────────────────────────────────────┘
```

**混合模式**：确定性流程（调引擎/拼接/后处理）硬编码在 `core/`，创意与判断决策（编剧/prompt 优化/审片/重做）交 agent。

---

## 3. 模型与引擎清单

### 3.1 已部署且运行中

| 模型 | 能力 | GPU | ComfyUI 端口 | API 端口 | 显存占用 | 模型文件 | 用途版本 |
|------|------|-----|-------------|---------|---------|---------|---------|
| **Wan2.2-TI2V-5B** | T2V（文生视频） | GPU2 | 8189 | 8890 | 17.8G | 17G（unet 9.4G + umt5 6.3G + vae 1.4G） | v0 主力 |
| **HunyuanVideo 13B** | T2V | GPU3 | 8190 | 8891 | 20.1G | 34G（unet 24G bf16 + llava 8.5G + clip_l 235M + vae 471M） | v0 备选/v1+ |
| **SDXL Base 1.0** | T2I（文生图） | GPU1 | 8191 | 8892 | 31.5G | 8.7G（单文件 6.5G） | v1 角色/场景图 |
| **FLUX.1-dev fp8** | T2I（文生图，高质量） | GPU0 | 8192 | 8893 | 36.2G | 16G（unet 12G + t5 4.6G + vae 320M） | v1 角色生成主力 |

- 中文 prompt：SDXL/FLUX 均经 `translate.py`（USTC deepseek-v4-flash）自动中→英翻译
- HunyuanVideo 中文 tokenizer bug 已修复（`LlamaTokenizerFast` → `PreTrainedTokenizerFast`）
- 所有 CLI/server 默认随机 seed + `--seed N` 复现，防 ComfyUI 缓存命中

### 3.2 已部署但未运行（随时可拉起）

| 模型 | 能力 | 端口 | 模型文件 | 说明 |
|------|------|------|---------|------|
| **MiniMax-H3** | T2V + 音画同步 | 8188/8889 | 70G | 生成较慢，v0 不用；音画同步能力待 v2 评估 |

### 3.3 马上部署（v0 基础设施阶段）

| 组件 | 能力 | 部署方式 | 资源 | 状态 |
|------|------|---------|------|------|
| **ffmpeg + sox** | 视频后处理 / CosyVoice 音频依赖 | `conda install -c conda-forge` | — | 🔄 安装中 |
| **moviepy** | Python 视频编辑（字幕/特效/转场高级封装） | `pip install` | — | 待装 |
| **CosyVoice 2** | TTS 文字转语音 + zero-shot 音色克隆 | conda env(python3.10) + modelscope 下模型 | GPU2:9880, ~5G | 🔄 部署中 |
| **MoneyPrinterTurbo** | 自动成片 pipeline（仅克隆研究架构） | git clone，不部署 | — | ✅ 已克隆 |

### 3.4 待部署（后续版本按需）

| 组件 | 能力 | 需要版本 | 模型来源 | 说明 |
|------|------|---------|---------|------|
| **TripoSplat** | 单图→3DGS 高斯 | v1 | hf-mirror 4 文件（dino_v3 + flux2-vae + triposplat_vae_decoder + triposplat_fp16） | 角色锚核心，RenderSplat 渲染各视角 |
| **RenderSplat** | 3DGS→任意视角图像（headless） | v1 | ComfyUI 内置节点 | 配合 CreateCameraInfo(yaw/pitch/fov) |
| **RIFE / FILM** | 光流插帧（软过渡/慢动作） | v2 | hf-mirror | ComfyUI FrameInterpolate 节点 |
| **faster-whisper** | STT 语音转文字 | v2+ | hf-mirror large-v3-turbo | 字幕时间轴备选方案（CosyVoice 时间戳不够用时） |
| **Hunyuan3Dv2** | 图→mesh 重建 | v3 | hf-mirror | 高质量资产导出，headless 渲染需 Isaac Sim/Blender |
| **爬虫库** | bs4/lxml/yt-dlp/feedparser | v4 | pip | 爬热点/参考图/人声 |

---

## 4. 版本路线

### v0 — 有声短片 MVP（基础设施阶段）⬅ 当前

**目标**：跑通"概念→有声短片"端到端，验证 agent 编排本地引擎 + 音频链路。

```
用户: concept="一只猫在月球上跳舞"
  │
  ├─[LLM deepseek-v4-flash] 编剧 → 分镜脚本(含旁白文本)
  ├─[LLM] 每镜 scene_desc → 英文 video_prompt
  ├─[Wan T2V 8189] 每镜生成视频片段（随机 seed 防缓存）
  ├─[CosyVoice 2 9880] 旁白文本 → 配音音频（带时间戳）
  ├─[多模态 LLM sonnet-4-6] 逐镜审片(抽4帧读图打分) → 不通过则调prompt+新seed重做(≤2次)
  ├─[ffmpeg+moviepy] 拼接片段 + 配音合成 + 字幕(用TTS时间戳) + 转场
  └─[元数据] output/{task_id}/meta.json + final.mp4
```

**范围**：纯 T2V 生成（无 I2V/3D），单角色无需一致性，每镜 ≤5s（Wan 121帧上限），2-5 镜头，10-30s 成片。

**验收**：输入一句中文概念 → 输出有声 mp4，无需人工干预，元数据完整可复现。

**详细设计**：见 [v0-design.md](./v0-design.md)

### v1 — 角色一致性

**目标**：同一角色跨镜头不漂移。

```
[FLUX 生角色参考图(单图或多视图)]
  → [TripoSplat: 角色→3DGS]  ← 角色锚，全片唯一
  → 每镜头:
      [RenderSplat: 3DGS→该角度参考帧]  (可合成 T2I 生成的背景)
      → [Wan I2V: 参考帧→视频片段(角色锁定)]
```

**新增依赖**：TripoSplat 4 文件、RenderSplat（ComfyUI 内置）、Wan I2V workflow

**关键**：RenderSplat 的 `bg_image` 合成 = 先 T2I 生成场景背景，再合成角色 → I2V，实现"角色走入场景"。

### v2 — 长视频

**目标**：超出单次生成上限的长片 + 完整后期。

- **RIFE 光流插帧**：片段间软过渡（替代硬拼接），也可做慢动作
- **ffmpeg 调色/配乐**：完整后期链路
- **STT 字幕**：若 CosyVoice 时间戳不够精确，引入 faster-whisper 对齐
- **MiniMax-H3 音画同步**：评估是否用于带原生音频的片段

**新增依赖**：RIFE 模型、faster-whisper（视字幕方案）、配乐素材库

### v3 — 2D→3D→新视角

**目标**：从 2D 内容重建 3D，生成原视角没有的新角度视频。

```
[多视角生成] → [Hunyuan3D mesh 重建] → [headless 渲染器出新视角]
  → [I2V 生成动态] → [拼接]
```

**新增依赖**：Hunyuan3D 模型、headless mesh 渲染器（待定：Isaac Sim / Blender / trimesh+pyrender）

**已知阻塞**：Hunyuan3D 出 mesh 生成可 headless，但 ComfyUI 的 mesh 渲染节点依赖浏览器 WebGL，无法 API 调用。需在 v3 启动前确定 headless 渲染方案。

### v4 — 营销号流水线

**目标**：批量自动化内容生产与发布。

- **爬热点**：feedparser/yt-dlp 抓热点话题、参考图、人声
- **声音克隆生产化**：从爬取人声克隆固定音色
- **异步任务制**：POST 提交 + GET 轮询（对齐 Seedance 风格），支持并发批量
- **自动发布**：跨平台上传（借鉴 MoneyPrinterTurbo 的发布模块）
- **FunClip 自动剪辑**：阿里同生态，ASR 驱动的智能裁剪

**新增依赖**：爬虫库、任务队列、发布 API、FunClip

---

## 5. GPU 与端口分配

| GPU | 显存 | 实例 | 端口 | API |
|-----|------|------|------|-----|
| GPU0 | 48G | FLUX.1-dev fp8 | 8192 | 8893 |
| GPU1 | 48G | SDXL Base 1.0 | 8191 | 8892 |
| GPU2 | 48G | Wan2.2-5B **+ CosyVoice 2** | 8189 / — | 8890 / 9880 |
| GPU3 | 48G | HunyuanVideo 13B | 8190 | 8891 |

- CosyVoice v0 放 GPU2（与 Wan 共享，流水线串行不抢资源，余 23G 够 TTS ~5G）
- MiniMax-H3（8188/8889）按需拉起，v0 不常驻
- TripoSplat（v1）拟放 GPU0 或 GPU1（视余量）

---

## 6. LLM 与 API

| 服务 | 用途 | 状态 |
|------|------|------|
| USTC deepseek-v4-flash | 编剧、prompt 优化、翻译 | ✅ 免费 |
| USTC claude-sonnet-4-6 | 多模态审片（读图+reasoning） | ✅ 免费 |
| USTC claude-haiku-4-5 | 快速审片备选 | ✅ 免费 |
| USTC qwen3.6-chat / qwen3.5 | 文本备选 | ✅ 免费 |
| USTC claude-opus-4-8 / glm-5.2 / k3 | — | ❌ 不可用 |
| ark/doubao | — | ❌ 订阅失效 |

- USTC API：`https://api.llm.ustc.edu.cn/v1`，key 存 `config/config.json`（不入 git）

---

## 7. 关键技术决策

1. **混合模式编排**：确定性流程硬编码，创意/判断交 agent（可控+灵活平衡）
2. **ComfyUI workflow JSON 模板 + 参数注入**：可视化调试，和 blueprint 对齐
3. **director + reviewer subagents**：opencode 原生模式，审片职责隔离
4. **v0 同步执行**：一条龙跑完，无任务队列（异步留 v4）
5. **CosyVoice v2 而非 v3**：稳定优先，社区部署经验多
6. **字幕先试 TTS 时间戳**：若 CosyVoice 返回词级时间戳则不需 STT，省一个依赖
7. **3DGS 路径优先 mesh**：RenderSplat 全程 headless 可 API 调用；mesh 渲染需浏览器，留 v3 解决
8. **每镜随机 seed**：防 ComfyUI 缓存命中秒出旧结果
9. **审片 3 次上限 + 最高分兜底**：防死循环，保证流水线不卡

---

## 8. 目录结构

```
vidance/
├── opencode.json              # opencode 配置：provider + agents + permissions
├── AGENTS.md                  # 项目约定 + 命令（供 agent 读）
├── .opencode/
│   ├── agents/                # director.md / reviewer.md
│   └── skills/                # scriptwriting.md / review.md
├── config/
│   └── config.json            # API key、引擎地址、模型名
├── core/                      # 编排层（硬编码骨架）
│   └── pipeline.py
├── utils/                     # 工具层
│   ├── comfy_api.py           # ComfyUI 客户端
│   ├── llm.py                 # USTC LLM（文本+多模态）
│   ├── tts.py                 # CosyVoice TTS 客户端
│   ├── ffmpeg_tools.py        # 后处理（抽帧/拼接/字幕/配音合成）
│   └── workflows/             # ComfyUI workflow JSON 模板
├── docs/
│   ├── roadmap.md             # 本文档
│   ├── v0-design.md           # v0 详细设计
│   ├── hierachy.md
│   ├── tech.md
│   └── experience.md
├── input/                     # 输入资源
└── output/ → /mnt/dataset/zxy/vidance/output/  # 软链到机械盘
```

---

## 9. 相关仓库与参考

| 仓库 | 位置 | 用途 |
|------|------|------|
| ComfyUI | `/mnt/disk_sdb/zxy/ComfyUI/` | 共用推理引擎，多实例 |
| CosyVoice | `/mnt/disk_sdb/zxy/CosyVoice/` | TTS 引擎（v0 部署） |
| MoneyPrinterTurbo | `/mnt/disk_sdb/zxy/MoneyPrinterTurbo/` | 仅研究架构（字幕双模式/VideoParams/skill） |

---

## 10. 部署记录

各生图/生视频模型部署细节见：
- `/mnt/disk_sdb/zxy/MiniMax-H3-部署记录.md`
- `/mnt/disk_sdb/zxy/SDXL-部署记录.md`
- `/mnt/disk_sdb/zxy/FLUX-部署记录.md`
