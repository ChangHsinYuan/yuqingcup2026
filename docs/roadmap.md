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
| 配音员 | TTS 双引擎 | edge-tts（自然在线）+ CosyVoice 2（本地克隆） |
| 审片员 | 多模态 LLM（haiku-4-5 主力 / sonnet-4-6 严格备选） | 逐帧读图打分，闭环重做 |
| 剪辑师 | ffmpeg + moviepy | 拼接/字幕/配乐/调色/转场 |

**核心创新**：3DGS 充当"角色锚"——每镜头起始帧由 RenderSplat 从同一 3DGS 按镜头角度渲染，喂 I2V 生成，避免跨镜头角色漂移。

---

## 2. 三层架构

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v0 ✅ 有声短片 → v1 ✅ 角色一致性 → v2 📐 长视频      │
│         v3 📐 2D→3D→新视角(⚠️阻塞) → v4 📐 营销号流水线    │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(reviewer) + skills         │
│           core/ Python 流水线骨架（混合模式）              │
├──────────────────────────────────────────────────────────┤
│  引擎层   T2I: SDXL / FLUX                                │
│         T2V: Wan2.2 / HunyuanVideo / MiniMax-H3           │
│         3D:  TripoSplat(3DGS) / Hunyuan3D(mesh) / RenderSplat │
│         音频: edge-tts(在线自然) + CosyVoice 2(本地克隆, 9880) │
│              faster-whisper (STT备选, v2+)                      │
│         后处理: ffmpeg + moviepy + RIFE(插帧)              │
│         LLM: USTC API (编剧 + 多模态审片)                  │
└──────────────────────────────────────────────────────────┘
```

**混合模式**：确定性流程（调引擎/拼接/后处理）硬编码在 `core/`，创意与判断决策（编剧/prompt 优化/审片/重做）交 agent。

---

## 3. 模型与引擎清单

### 3.1 已部署且运行中

| 模型 | 能力 | GPU | ComfyUI 端口 | API 端口 | 显存占用 | 模型文件 | 用途版本 |
|------|------|-----|-------------|---------|---------|---------|---------|
| **Wan2.2-TI2V-5B** | T2V + I2V（文/图生视频） | GPU2 | 8189 | 8890 | 17.8G | 17G（unet 9.4G + umt5 6.3G + vae 1.4G） | v0 主力 / v1 I2V 复用 |
| **HunyuanVideo 13B** | T2V | GPU3 | 8190 | 8891 | 20.1G | 34G（unet 24G bf16 + llava 8.5G + clip_l 235M + vae 471M） | v0 备选/v1+ |
| **SDXL Base 1.0** | T2I（文生图） | GPU1 | 8191 | 8892 | 31.5G | 8.7G（单文件 6.5G） | v1 备选角色/场景图（FLUX 为主力） |
| **FLUX.1-dev fp8** | T2I（文生图，高质量） | GPU0 | 8192 | 8893 | 36.2G | 16G（unet 12G + t5 4.6G + vae 320M） | v1 角色生成主力 |
| **TripoSplat** | 单图→3DGS 高斯 | GPU0 | 8192（共用FLUX） | — | ~4G（运行时） | 3.6G（5文件：triposplat_fp16 + dino_v3 + triposplat_vae_decoder + flux2-vae + birefnet） | v1 角色锚核心 |

- 中文 prompt：SDXL/FLUX 均经 `translate.py`（USTC deepseek-v4-flash）自动中→英翻译
- HunyuanVideo 中文 tokenizer bug 已修复（`LlamaTokenizerFast` → `PreTrainedTokenizerFast`）
- 所有 CLI/server 默认随机 seed + `--seed N` 复现，防 ComfyUI 缓存命中

### 3.2 已部署但未运行（随时可拉起）

| 模型 | 能力 | 端口 | 模型文件 | 说明 |
|------|------|------|---------|------|
| **MiniMax-H3** | T2V + 音画同步 | 8188/8889 | 70G | 生成较慢，v0 不用；音画同步能力待 v2 评估 |

### 3.3 已部署完成（v0 基础设施）

| 组件 | 能力 | 部署方式 | 资源 | 状态 |
|------|------|---------|------|------|
| **ffmpeg 8.0.1 + sox 14.4.2** | 视频后处理 / CosyVoice 音频依赖 | conda install（cosyvoice env），软链 `~/.local/bin/` | — | ✅ |
| **moviepy 2.1.2** | Python 视频编辑（字幕/特效/转场高级封装） | pip install（主环境），Pillow 降到 11.3.0 | — | ✅ |
| **edge-tts 7.2.8** | 微软神经语音（在线 TTS，自然音色） | pip install（cosyvoice env） | CPU, 0 显存 | ✅ |
| **CosyVoice 2** | TTS 文字转语音 + zero-shot 音色克隆 | conda env(python3.10) + modelscope 下模型 | GPU2:9880, ~2.5G 显存 | ✅ |
| **MoneyPrinterTurbo** | 自动成片 pipeline（仅克隆研究架构） | git clone，不部署 | — | ✅ 已克隆 |

> TTS 服务为双引擎统一封装（`utils/tts_server.py`）：edge-tts 走 `edge-*` 音色前缀，CosyVoice 走 `cosy-*` 前缀，对外接口一致。

### 3.4 待部署（后续版本按需）

| 组件 | 能力 | 需要版本 | 模型来源 | 说明 |
|------|------|---------|---------|------|
| **RIFE / FILM** | 光流插帧（软过渡/慢动作） | v2 | hf-mirror | ComfyUI FrameInterpolate 节点 |
| **faster-whisper** | STT 语音转文字 | v2+ | hf-mirror large-v3-turbo | 字幕时间轴备选方案（CosyVoice 时间戳不够用时） |
| **Hunyuan3Dv2** | 图→mesh 重建 | v3 | hf-mirror | 高质量资产导出，headless 渲染需 Isaac Sim/Blender |
| **爬虫库** | bs4/lxml/yt-dlp/feedparser | v4 | pip | 爬热点/参考图/人声 |

---

## 4. 版本路线

### v0 — 有声短片 MVP ✅ 已完成

**目标**：跑通"概念→有声短片"端到端，验证 agent 编排本地引擎 + 音频链路。

**状态**：2026-09-07 端到端验证通过。概念"一只猫在月球上跳舞" → 13.3s 成片（3 镜，审片 7/7/7，meta.json 完整）。TTS 双引擎上线（edge-tts 16 音色 + CosyVoice 2 + 自定义克隆通道），edge-moe 重配音验证通过。

```
用户: concept="一只猫在月球上跳舞"
  │
  ├─[LLM deepseek-v4-flash] 编剧 → 分镜脚本(含旁白文本)
  ├─[LLM] 每镜 scene_desc → 英文 video_prompt
  ├─[Wan T2V 8189] 每镜生成视频片段（随机 seed 防缓存）
  ├─[TTS 9880 双引擎] 旁白文本 → 配音音频（edge-tts 自然 / CosyVoice 克隆）
  ├─[多模态 LLM haiku-4-5] 逐镜审片(抽4帧读图打分,4s/镜) → 不通过则调prompt+新seed重做(≤2次)
  ├─[ffmpeg+moviepy] 拼接片段 + 配音合成 + 字幕(用TTS时间戳) + 转场
  └─[元数据] output/{task_id}/meta.json + final.mp4
```

**范围**：纯 T2V 生成（无 I2V/3D），单角色无需一致性，每镜 ≤5s（Wan 121帧上限），2-5 镜头，10-30s 成片。

**详细设计与验证记录**：见 [v0-design.md](./v0-design.md)

### v1 — 角色一致性 ✅ 已完成（含 v1.1 优化）

**目标**：同一角色跨镜头不漂移。

**状态**：2026-09-08 端到端验证通过。v1 角色锚全链路跑通（FLUX→TripoSplat→3DGS→RenderSplat→Wan I2V），I2V bug 已修复（Wan22ImageToVideoLatent，首帧相关性 0.99+）。**v1.1 优化完成**：character-mode 开关（auto/3dgs/flux），flux 模式绕过 3DGS 瓶颈（4 镜 16s 成片，I2V 相关性 0.993-0.998，审片全 7 分一次过），auto 模式 3DGS 审查不过自动降级 flux。**3DGS 位姿修复完成**：PCA 自动对齐（Z-alignment 0.774→1.000）+ 接地合成（crop→scale 55%→ground 88%），review_character 首次通过 score=7（原 score=2）。M1-M8 全部完成。

```
[FLUX 生角色参考图(1024×1024)]
  → character-mode 分流:
    flux: 每镜 FLUX 直接生成角色+场景完整图 → Wan I2V
    3dgs: [TripoSplat: 角色→3DGS PLY] → 每镜 RenderSplat 按角度渲染 → composite → Wan I2V
          (所有镜头强制 3DGS)
    auto: 先走 3dgs + review_character 审查 → 不过则降级 flux
  → 每镜头: [Wan I2V: 参考帧→视频(832×480×81-120帧)]
       → [TTS] + [抽帧2张] + [LLM审片5维(含character_consistency)] → 不通过重试≤2次
  → [ffmpeg+moviepy 合成 final.mp4]
```

**已知限制**：
- 3DGS 重建质量仍是瓶颈：TripoSplat 262K 高斯渲染稀疏 → v1.1 通过 PCA 对齐+接地合成修复位姿（review_character score 2→7），飞点过滤+超采样+大高斯提升表面平滑度（LLM 确认噪声显著减少），flux 模式绕过质量瓶颈
- I2V 已修复：Wan22ImageToVideoLatent（48ch + noise_mask inpainting），首帧相关性 0.99+
- 3DGS 镜头类型受限：只能全身体远景/中景，无法特写
- flux 模式侧面/背面镜头（yaw≠0）角色可能走样，无 3D 约束 → v2 探索 IP-Adapter
- 多模态审片 API 不稳定（60s timeout + retry + safe fallback）

**新增依赖**：TripoSplat 5 文件（含 BiRefNet 去背）、RenderSplat（ComfyUI 内置）、Wan I2V workflow（复用 5B ti2v，无需下 14B）

**关键**：v1.1 flux 模式 = 每镜 FLUX 直接生成角色+场景图 → I2V，绕过 3DGS 重建瓶颈，质量更稳定。3DGS 模式经 PCA 对齐+接地合成后 review_character 首次通过（score=7），角色站立接地不再悬浮。

**详细设计**：见 [v1-design.md](./v1-design.md)

### v2 — 长视频（🚧 进行中）

**目标**：超出单次生成上限的长片 + 完整后期。**建立在 v1 角色一致性之上**（长片仍需跨镜头角色不漂移）。

**状态**：M1 RIFE 光流插帧已完成并验证通过（2026-09-09）。3 镜+2 过渡=15.4s 成片，镜头间光流过渡 0.375s@24fps。剩余 M2-M5 进行中。

- **M1 RIFE 光流插帧** ✅：片段间软过渡（替代硬拼接），也可做慢动作。`rife_transition.json` workflow + `RIFEClient` + pipeline 集成，config `rife.enabled/multiplier=8`
- **M2 镜头并行** 🔲：v0/v1 同步执行，v2 引入镜头级 ThreadPool 并行生成
- **M3 ffmpeg 调色** 🔲：LUT 调色 + 开源 LUT 库自动下载
- **M4 配乐 ducking** 🔲：BGM 自动下载 + 配音时 ducking 降音量
- **M5 STT 字幕兜底** 🔲：faster-whisper 对齐（CosyVoice 时间戳不够用时）

**新增依赖**：RIFE 模型 ✅、faster-whisper（视字幕方案）、配乐素材库

**详细设计**：见 [v2-design.md](./v2-design.md)（📐 设计完成）

### v3 — 2D→3D→新视角（📐 设计完成，⚠️ 有阻塞）

**目标**：从 2D 内容重建 3D，生成原视角没有的新角度视频。

```
[多视角生成] → [Hunyuan3D mesh 重建] → [headless 渲染器出新视角]
  → [I2V 生成动态] → [拼接]
```

**新增依赖**：Hunyuan3D 模型、headless mesh 渲染器（待定：Isaac Sim / Blender / trimesh+pyrender）

**已知阻塞**：Hunyuan3D 出 mesh 生成可 headless，但 ComfyUI 的 mesh 渲染节点依赖浏览器 WebGL，无法 API 调用。需在 v3 启动前确定 headless 渲染方案。

**详细设计**：见 [v3-design.md](./v3-design.md)（📐 设计完成，⚠️ headless 渲染阻塞待解）

### v4 — 营销号流水线（📐 设计完成）

**目标**：批量自动化内容生产与发布。

- **爬热点**：feedparser/yt-dlp 抓热点话题、参考图、人声
- **声音克隆生产化**：从爬取人声克隆固定音色
- **异步任务制**：POST 提交 + GET 轮询（对齐 Seedance 风格），支持并发批量
- **自动发布**：跨平台上传（借鉴 MoneyPrinterTurbo 的发布模块）
- **FunClip 自动剪辑**：阿里同生态，ASR 驱动的智能裁剪

**新增依赖**：爬虫库、任务队列、发布 API、FunClip

**详细设计**：见 [v4-design.md](./v4-design.md)（📐 设计完成）

---

## 5. GPU 与端口分配

| GPU | 显存 | 实例 | 端口 | API |
|-----|------|------|------|-----|
| GPU0 | 48G | FLUX.1-dev fp8 | 8192 | 8893 |
| GPU1 | 48G | SDXL Base 1.0 | 8191 | 8892 |
| GPU2 | 48G | Wan2.2-5B **+ CosyVoice 2** | 8189 / — | 8890 / 9880 |
| GPU3 | 48G | HunyuanVideo 13B | 8190 | 8891 |

- CosyVoice 放 GPU2（与 Wan 共享，流水线串行不抢资源，余 23G 够 TTS ~2.5G）
- edge-tts 在线引擎不占 GPU（CPU + 网络）
- MiniMax-H3（8188/8889）按需拉起，v0 不常驻
- TripoSplat 与 FLUX 共用 GPU0:8192 同一 ComfyUI 实例（串行调用，已验证）

---

## 6. LLM 与 API

| 服务 | 用途 | 状态 |
|------|------|------|
| USTC deepseek-v4-flash | 编剧、prompt 优化、翻译 | ✅ 免费 |
| USTC claude-haiku-4-5 | 多模态审片（主力，4s/镜） | ✅ 免费 |
| USTC claude-sonnet-4-6 | 严格审片备选（review_strict，534s/镜） | ✅ 免费 |
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
5. **TTS 双引擎**：edge-tts 默认（自然省 GPU）+ CosyVoice 兜底/克隆；音色按前缀路由（edge-* / cosy-*）
6. **CosyVoice v2 而非 v3**：稳定优先，社区部署经验多
7. **字幕用 TTS 时间戳**：edge-tts 词级 + CosyVoice 句子级，均精确，v0 不引入 STT
8. **审片用 haiku-4-5**：4s/镜（sonnet 534s/镜太慢），质量够用，sonnet 留 review_strict
9. **3DGS 优先于 mesh 路径**：RenderSplat 全程 headless 可 API 调用；mesh 渲染需浏览器 WebGL，留 v3 解决 headless 方案
10. **每镜随机 seed**：防 ComfyUI 缓存命中秒出旧结果
11. **审片 3 次上限 + 最高分兜底**：防死循环，保证流水线不卡
12. **存储分层**：代码/配置/小素材放 sdb（快盘），模型/视频/音频/产出放 mergerfs 机械盘，软链统一访问
13. **3DGS 角色锚**（v1）：全片唯一 3DGS，每镜 RenderSplat 按角度渲染参考帧喂 I2V，锁定跨镜头角色
14. **RenderSplat bg_image 合成**（v1）：T2I 生成无角色背景 + 角色按视角 alpha 混合 → 合成参考帧，实现"角色走入场景"
15. **复用 Wan 5B ti2v 做 I2V**（v1）：ti2v 本身支持 I2V（`Wan22ImageToVideoLatent` 节点，48ch latent + noise_mask inpainting），无需下 14B
16. **审片加 character_consistency 维度**（v1）：与角色参考图对比，五维打分（v0 四维 + 角色一致性）

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
│   └── config.json            # API key、引擎地址、模型名、音色配置
├── core/                      # 编排层（硬编码骨架）
│   └── pipeline.py
├── utils/                     # 工具层
│   ├── comfy_api.py           # ComfyUI 客户端
│   ├── llm.py                 # USTC LLM（文本+多模态）
│   ├── tts.py                 # TTS 客户端（双引擎：edge-tts + CosyVoice）
│   ├── tts_server.py          # TTS FastAPI 服务（双引擎，GPU2:9880）
│   ├── ffmpeg_tools.py        # 后处理（抽帧/拼接/字幕/配音合成）
│   └── workflows/             # ComfyUI workflow JSON 模板
├── voices/                    # 自定义 CosyVoice 克隆音色素材
├── docs/
│   ├── roadmap.md             # 本文档
│   ├── v0-design.md           # v0 详细设计（✅ 已完成）
│   ├── v1-design.md           # v1 角色一致性设计（📐 设计完成）
│   ├── v2-design.md           # v2 长视频+完整后期设计（📐 设计完成）
│   ├── v3-design.md           # v3 2D→3D→新视角设计（📐 设计完成，⚠️ 阻塞）
│   ├── v4-design.md           # v4 营销号流水线设计（📐 设计完成）
│   ├── hierachy.md
│   ├── tech.md
│   └── experience.md
├── input/                     # 输入资源
├── voice_samples/ → /mnt/dataset/...  # 音色试听样本（软链）
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
