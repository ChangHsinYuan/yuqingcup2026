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
│  产品层   v0 ✅ 有声短片 → v1 ✅ 角色一致性 → v2 ✅ 长视频       │
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
│              faster-whisper (STT兜底, large-v3-turbo, 9880)     │
│         后处理: ffmpeg + moviepy + RIFE(插帧) + LUT(调色) + BGM(配乐) │
│         LLM: USTC API (编剧 + 多模态审片)                  │
└──────────────────────────────────────────────────────────┘
```

**混合模式**：确定性流程（调引擎/拼接/后处理）硬编码在 `core/`，创意与判断决策（编剧/prompt 优化/审片/重做）交 agent。

---

## 3. 模型与引擎清单

### 3.1 视频生成模型（ComfyUI 实例）

| 模型 | 能力 | GPU | 端口 | 显存 | 模型文件 | 大小 | 状态 |
|------|------|-----|------|------|---------|------|------|
| **Wan2.2-TI2V-5B** | T2V + I2V | GPU2 | 8189 | 18G | `wan2.2_ti2v_5B_fp16`(9.4G) + `umt5_xxl_fp8`(6.3G) + `wan2.2_vae`(1.4G) | 17G | 运行中 |
| **MiniMax-H3 ref2va** | 参考图→视频+原生音频 | GPU0 | 8188 | 46G | `minimax_h3_ref2va_int8`(20G) + `qwen3vl_32b_int8`(26G) + `video_vae_fp16`(4.9G) + `audio_vae_fp32`(578M) | 70G | **运行中（custom 主力）** |
| **HunyuanVideo 13B** | T2V | GPU3 | 8190 | 20G | `hunyuan_video_t2v_720p_bf16`(24G) + `llava_llama3_fp8`(8.5G) + `clip_l`(235M) + `vae`(471M) | 34G | 运行中 |
| **SDXL Base 1.0** | T2I | GPU1 | 8191 | 7G | `sd_xl_base_1.0.safetensors`(6.5G) | 6.5G | 运行中 |
| **FLUX.1-dev fp8** | T2I | GPU0 | 8192 | — | `flux1-dev-fp8`(12G) + `t5xxl_fp8`(4.6G) + `ae`(320M) | 16G | **已停** |
| **TripoSplat** | 单图→3DGS | GPU0 | 8192 | ~4G | `triposplat_fp16`(707M) + `dino_v3_vit_h`(1.6G) + `vae_decoder`(550M) + `flux2-vae`(321M) + `birefnet`(424M) | 3.6G | 随 FLUX |
| **MiniMax-H3 fl2va** | 纯文本→视频 | GPU0 | 8188 | — | `minimax_h3_fl2va_pruned_int8`(20G) | 20G | 备用（同实例） |

- Wan T2V/I2V：auto/quick 模式主力，1280×704@24fps
- H3 ref2va：custom 模式主力，1344×768@24fps + 原生音频，Qwen3-VL-32B 文本编码器原生支持中文
- H3 prompt 用 `<Picture i>` 标签引用参考图，ref_image_size: match(快)/max(2048px 高保真)
- HunyuanVideo/SDXL：备选引擎，目前未接入流水线
- FLUX 已停（8192 端口无进程），TripoSplat 随 FLUX 共实例；auto 模式 flux 角色锚需重新启动 8192
- 每镜随机 seed（防 ComfyUI 缓存命中）

### 3.2 音频模型

| 模型 | 能力 | 端口 | GPU | 显存 | 大小 | 状态 |
|------|------|------|------|------|------|------|
| **CosyVoice 2 (0.5B)** | TTS + zero-shot 音色克隆 | 9880 | GPU2 | 2.5G | 4.4G（llm.pt 1.9G + flow.pt 430M + hift.pt 80M + tokenizer 474M×2） | 运行中 |
| **faster-whisper large-v3-turbo** | STT 语音转文字 | 内嵌 | 共享 | 1.5G | 1.6G（int8_float16 model.bin） | 按需（`--stt`） |
| **edge-tts** | 在线 TTS（微软神经语音） | 9880 | CPU | 0 | 0（在线） | 运行中 |

- TTS 双引擎统一封装（`utils/tts_server.py`）：edge-* 走 edge-tts，cosy-* 走 CosyVoice
- STT 模型在 hf_cache，`--stt` 时 faster-whisper 内嵌加载（不需独立服务）
- custom 模式用 H3 原生音频，不使用 TTS

### 3.3 插帧模型

| 模型 | 能力 | 位置 | GPU | 大小 | 状态 |
|------|------|------|------|------|------|
| **RIFE v4.26** | 光流插帧（镜头间过渡） | ComfyUI `models/frame_interpolation/` | GPU2 | 11M | 运行中 |
| **RIFE v4.26 heavy** | 光流插帧（高质量变体） | 同上 | GPU2 | 11M | 备用 |

### 3.4 程序生成资产（非模型）

| 资产 | 位置 | 大小 | 说明 |
|------|------|------|------|
| 3D LUT ×6 | `utils/luts/` | 948K×6 | cinematic/warm/cool/vintage/vivid/soft（`gen_luts.py` 生成） |
| BGM | 运行时生成 | 0 | numpy 合成 5 种 mood（`music.py`），留 `bgm/` 自定义目录 |

### 3.5 已下载未使用

| 模型 | 位置 | 大小 | 说明 |
|------|------|------|------|
| Qwen3.6-35B-A3B | `/mnt/dataset/zxy/Qwen3.6-35B-A3B/` | 67G | 未接入 |
| MiniMax-H3 原始权重 | `/mnt/dataset/zxy/MiniMax-H3/` | 465G | 已转 int8 ComfyUI 格式（70G），原始可删 |

### 3.6 LLM（远程 API，不本地部署）

| 模型 | 用途 |
|------|------|
| deepseek-v4-flash | 编剧、prompt 优化、LUT 风格选择、BGM mood 选择 |
| claude-haiku-4-5 | 多模态审片（主力，4s/镜） |
| claude-sonnet-4-6 | 严格审片备选（534s/镜太慢） |

> USTC API：`https://api.llm.ustc.edu.cn/v1`，key 存 `config/config.json`

### 3.7 待部署（后续版本按需）

| 组件 | 能力 | 需要版本 | 说明 |
|------|------|---------|------|
| **Hunyuan3Dv2** | 图→mesh 重建 | v3 | 高质量资产导出，headless 渲染需 Isaac Sim/Blender |
| **爬虫库** | bs4/lxml/yt-dlp/feedparser | v4 | 爬热点/参考图/人声 |

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

### v2 — 长视频（✅ 核心完成，v2.1 backlog）

**目标**：超出单次生成上限的长片 + 完整后期。**建立在 v1 角色一致性之上**（长片仍需跨镜头角色不漂移）。

**状态**：2026-09-09 M1-M5 核心完成，端到端验证通过（3 次测试，10-20s 成片/3-4 镜）。RIFE 过渡 + 并行预取 + LUT 调色 + BGM ducking + STT 字幕全链路跑通。编码 yuv420p + 字幕重叠 bug 已修复。

**未验证/未实现（→ v2.1 backlog）**：
- 长片验证（5-15 镜/60s+）：LLM prompt 硬编码 "2-5个镜头"（`llm.py:116`），未跑过长片
- 单镜慢动作：`rife.py:91` `slowmo()` 已写好但 pipeline 未接线
- per-shot 过渡类型：pipeline 对所有镜头统一 RIFE，不读 `transition_out` 字段
- 后期审片 4 维（color/bgm/transition/audio）：未实现
- music subagent：简化为 pipeline 内联 LLM 调用（功能等价）
- MiniMax-H3 评估（M6）：✅ 已完成，H3 ref2va 接入 custom 模式，13 个分镜脚本测试通过

- **M1 RIFE 光流插帧** ✅：镜头间光流过渡（rife_v4.26，multiplier=8，0.375s@24fps），替代 crossfade 硬拼接。`rife_transition.json` workflow + `RIFEClient` + pipeline 集成
- **M2 镜头并行预取** ✅：ThreadPoolExecutor 并行预取所有镜头的 FLUX 参考帧（GPU0）+ TTS 配音（9880），与 Wan I2V（GPU2）串行执行不冲突，总耗时显著缩短
- **M3 ffmpeg 调色** ✅：6 种 3D LUT（cinematic/warm/cool/vintage/vivid/soft），纯 numpy 生成（`gen_luts.py`），LLM 根据脚本内容自动选风格，`--lut` 手动指定
- **M4 配乐 ducking** ✅：5 种 BGM（calm/uplifting/mysterious/dramatic/playful），纯 numpy 合成（`music.py`），ffmpeg sidechaincompress 配音时自动降 BGM 音量，LLM 自动选 mood
- **M5 faster-whisper STT** ✅：large-v3-turbo 模型（int8_float16 GPU），成片音频转写→SRT→重新烧录字幕，`--stt` 开关，TTS 时间戳的兜底方案

**新增依赖**：RIFE 模型 ✅、faster-whisper ✅、scipy（BGM 合成）、无外部下载（LUT+BGM 均程序生成）

**CLI 新增参数**：`--no-rife` / `--lut <style>` / `--no-color` / `--bgm <mood>` / `--no-bgm` / `--stt`（现通过统一入口 `python core/vidance.py auto/custom/quick` 使用，详见 [usage.md](./usage.md)）

**详细设计**：见 [v2-design.md](./v2-design.md)（✅ 核心完成，v2.1 backlog 见 §12.2）

### v2.5 — 多入口架构与 H3 引擎接入（✅ 已完成）

**目标**：不是加功能，而是重构入口和引擎层——统一 CLI 多子命令 + H3 ref2va 新引擎 + 后处理模块化。

**状态**：2026-09-11 端到端验证通过（13 个分镜脚本 / 32 镜 / ~96s 成片，双角色跨镜一致）。

```
统一入口 vidance.py
  ├─ auto   → pipeline.py → LLM编剧 → FLUX/Wan → TTS → 审片 → PostProcessor → 成片
  ├─ custom → custom_gen.py → 脚本解析 → H3 ref2va(多角色参考图+原生音频) → PostProcessor → 成片
  └─ quick  → pipeline.py → LLM编剧 → Wan T2V → TTS → 成片（无后处理）
```

- **统一 CLI**：`vidance.py` 三子命令（auto/custom/quick），argparse parent parser 共享后处理参数
- **H3 ref2va**：参考图每步去噪注入锁定角色身份，1344×768 + 原生音频，`<Picture i>` 标签引用多角色
- **PostProcessor**：从 Pipeline 抽出 RIFE/LUT/BGM/STT 为独立类，auto 和 custom 共享
- **多角色**：custom 模式支持最多 10 张参考图，双角色（豆包+奶蛙）跨 32 镜验证通过
- **中文 prompt 直传**：Qwen3-VL-32B 原生中文，省翻译步骤
- 向后兼容旧入口（pipeline.py / custom_gen.py 仍可直跑）

**详细设计**：见 [v2.5-design.md](./v2.5-design.md)

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

| GPU | 显存 | 实例 | 端口 | 状态 |
|-----|------|------|------|------|
| GPU0 | 48G | MiniMax-H3 ref2va | 8188 | 运行中（custom 主力） |
| GPU1 | 48G | SDXL Base 1.0 | 8191 | 运行中（+ Isaac Sim + aluupy 训练） |
| GPU2 | 48G | Wan2.2-5B + CosyVoice 2 + RIFE | 8189 / 9880 | 运行中（+ aluupy 训练） |
| GPU3 | 48G | HunyuanVideo 13B | 8190 | 运行中（+ aluupy 训练） |

- **FLUX.1-dev fp8 (8192) 已停**：auto 模式 `--character-mode flux` 需先重启 8192（TripoSplat 随 FLUX 共实例）
- CosyVoice 放 GPU2（与 Wan 共享，流水线串行不抢资源）
- edge-tts 在线引擎不占 GPU（CPU + 网络）
- faster-whisper STT 按需加载，共享 GPU 显存（不独立常驻）
- aluupy 在 GPU1/2/3 有训练任务，不能动

---

## 6. LLM 与 API

| 服务 | 用途 | 状态 |
|------|------|------|
| USTC deepseek-v4-flash | 编剧、prompt 优化、LUT 风格选择、BGM mood 选择 | ✅ 免费 |
| USTC claude-haiku-4-5 | 多模态审片（主力，4s/镜） | ✅ 免费 |
| USTC claude-sonnet-4-6 | 严格审片备选（review_strict，534s/镜太慢） | ✅ 免费 |

> USTC API：`https://api.llm.ustc.edu.cn/v1`，key 存 `config/config.json`（不入 git）

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
├── core/                      # 编排层
│   ├── vidance.py             # 统一 CLI 入口（auto/custom/quick 子命令）
│   ├── pipeline.py            # auto 模式流水线主控（内部模块）
│   ├── custom_gen.py          # custom 模式 H3 ref2va 生成（内部模块）
│   └── postprocess.py         # 共享后处理（RIFE/LUT/BGM/STT）
├── utils/                     # 工具层
│   ├── comfy_api.py           # ComfyUI 客户端（T2V/I2V/TripoSplat/FLUX/H3 ref2va）
│   ├── llm.py                 # USTC LLM（文本+多模态）
│   ├── tts.py                 # TTS 客户端（双引擎：edge-tts + CosyVoice）
│   ├── tts_server.py          # TTS FastAPI 服务（双引擎，GPU2:9880）
│   ├── splat_renderer.py      # 3DGS PLY 多角度渲染器
│   ├── ffmpeg_tools.py        # 后处理（抽帧/拼接/字幕/配音合成）
│   ├── rife.py                # RIFE 插帧客户端（镜头间过渡 + slowmo）
│   ├── gen_luts.py            # 生成 3D LUT .cube 文件（6 种风格）
│   ├── music.py               # 生成环境配乐 BGM（5 种风格）
│   ├── stt.py                 # faster-whisper STT 转写
│   ├── luts/                  # LUT 文件目录
│   └── workflows/             # ComfyUI workflow JSON 模板
├── voices/                    # 自定义 CosyVoice 克隆音色素材
├── bgm/                       # 自定义 BGM 素材目录
├── input/                     # 输入资源（参考图 + 分镜脚本）
├── docs/
│   ├── roadmap.md             # 本文档
│   ├── usage.md               # 使用指南
│   ├── v0-design.md           # v0 详细设计（✅ 已完成）
│   ├── v1-design.md           # v1 角色一致性设计（✅ 已完成）
│   ├── v2-design.md           # v2 长视频+完整后期设计（✅ 已完成）
│   ├── v2.5-design.md         # v2.5 多入口架构+H3引擎接入（✅ 已完成）
│   ├── deployed-models.md     # 已部署模型清单
│   ├── v3-design.md           # v3 2D→3D→新视角设计（📐 设计完成，⚠️ 阻塞）
│   ├── v4-design.md           # v4 营销号流水线设计（📐 设计完成）
│   ├── hierachy.md
│   ├── tech.md
│   └── experience.md
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

各模型部署细节见 `/mnt/disk_sdb/zxy/` 下：
- `MiniMax-H3-部署记录.md` — H3 ref2va/fl2va + Qwen3-VL-32B + 双 VAE
- `Wan2.2-部署记录.md` — Wan2.2-TI2V-5B + UMT5-XXL + VAE
- `HunyuanVideo-部署记录.md` — HunyuanVideo 13B + LLaVA-Llama3 + CLIP-L + VAE
- `SDXL-部署记录.md` — SDXL Base 1.0
- `FLUX-部署记录.md` — FLUX.1-dev fp8 + T5-XXL + VAE（含 TripoSplat 共实例配置）

> 模型文件统一存 `/mnt/dataset/zxy/<Model>-ComfyUI/`（mergerFS 机械盘），通过 `extra_model_paths.yaml` 映射到 ComfyUI。
