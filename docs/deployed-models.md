# Vidance 已部署模型清单

> 最后更新：2026-09-11

所有模型文件存 `/mnt/dataset/zxy/<Model>-ComfyUI/`（mergerFS 机械盘），通过 ComfyUI `extra_model_paths.yaml` 映射。代码/配置放 sdb 快盘，模型/产出放 mergerFS 机械盘。

---

## 1. 视频生成模型（ComfyUI 实例）

| 模型 | 能力 | 端口 | GPU | 显存 | 模型文件 | 大小 | 状态 |
|------|------|------|------|------|---------|------|------|
| **Wan2.2-TI2V-5B** | T2V + I2V | 8189 | GPU2 | 18G | `wan2.2_ti2v_5B_fp16`(9.4G) + `umt5_xxl_fp8`(6.3G) + `wan2.2_vae`(1.4G) | 17G | 运行中 |
| **MiniMax-H3 ref2va** | 参考图→视频+原生音频 | 8188 | GPU0 | 46G | `minimax_h3_ref2va_int8`(20G) + `qwen3vl_32b_int8`(26G) + `video_vae_fp16`(4.9G) + `audio_vae_fp32`(578M) | 70G | **运行中（custom 主力）** |
| **MiniMax-H3 fl2va** | 纯文本→视频 | 8188 | GPU0 | — | `minimax_h3_fl2va_pruned_int8`(20G) | 20G | 备用（同实例） |
| **HunyuanVideo 13B** | T2V | 8190 | GPU3 | 20G | `hunyuan_video_t2v_720p_bf16`(24G) + `llava_llama3_fp8`(8.5G) + `clip_l`(235M) + `vae`(471M) | 34G | 运行中（未接入流水线） |
| **SDXL Base 1.0** | T2I | 8191 | GPU1 | 7G | `sd_xl_base_1.0.safetensors`(6.5G) | 6.5G | 运行中（未接入流水线） |
| **FLUX.1-dev fp8** | T2I | 8192 | GPU0 | — | `flux1-dev-fp8`(12G) + `t5xxl_fp8`(4.6G) + `ae`(320M) | 16G | **已停** |
| **TripoSplat** | 单图→3DGS | 8192 | GPU0 | ~4G | `triposplat_fp16`(707M) + `dino_v3_vit_h`(1.6G) + `vae_decoder`(550M) + `flux2-vae`(321M) + `birefnet`(424M) | 3.6G | 随 FLUX 共实例 |

### 各模型用途说明

- **Wan2.2-TI2V-5B**：auto/quick 模式主力。1280×704@24fps，T2V + I2V 双能力（ti2v = text+image to video）。auto 模式 `--character-mode flux` 用 I2V 路径（`Wan22ImageToVideoLatent` 节点，48ch latent + noise_mask inpainting，首帧相关性 0.99+）。
- **MiniMax-H3 ref2va**：custom 模式主力。1344×768@24fps + 原生音频。每步去噪注入参考图锁定角色身份，支持最多 10 张参考图，用 `<Picture i>` 标签引用。文本编码器 Qwen3-VL-32B 原生支持中文，prompt 直传不翻译。`ref_image_size`: match（快，~6min/shot）/ max（2048px 高保真，慢 2-3x）。
- **MiniMax-H3 fl2va**：纯文本→视频（无参考图条件），与 ref2va 共享同一 ComfyUI 实例（8188），目前备用。
- **HunyuanVideo 13B**：T2V 备选引擎，720p 高质量，但生成慢、显存大，目前未接入流水线。中文 tokenizer bug 已修复（`LlamaTokenizerFast` → `PreTrainedTokenizerFast`）。
- **SDXL Base 1.0**：T2I 备选，v1 设计中作为 FLUX 的备选角色/场景图生成器，目前未接入。
- **FLUX.1-dev fp8**：auto 模式 `--character-mode flux` 的角色+场景图生成器（1024×1024 角色图，832×480 场景图）。**目前 8192 端口无进程，需手动重启才能用 flux 模式**。
- **TripoSplat**：auto 模式 `--character-mode 3dgs/auto` 的角色锚核心，单图→3DGS 高斯点云（262K 高斯）。与 FLUX 共用 8192 实例（串行不抢资源），随 FLUX 一起停/启。

### 模型文件路径

```
/mnt/dataset/zxy/
├── Wan-ComfyUI/
│   ├── diffusion_models/wan2.2_ti2v_5B_fp16.safetensors    (9.4G)
│   ├── text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors (6.3G)
│   └── vae/wan2.2_vae.safetensors                            (1.4G)
├── MiniMax-H3-ComfyUI/
│   ├── diffusion_models/
│   │   ├── minimax_h3_ref2va_pruned_int8_convrot.safetensors (20G)
│   │   └── minimax_h3_fl2va_pruned_int8_convrot.safetensors  (20G)
│   ├── text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors (26G)
│   └── vae/
│       ├── minimax_h3_video_vae_fp16.safetensors  (4.9G)
│       └── minimax_h3_audio_vae_fp32.safetensors  (578M)
├── HunyuanVideo-ComfyUI/
│   ├── diffusion_models/hunyuan_video_t2v_720p_bf16.safetensors (24G)
│   ├── text_encoders/
│   │   ├── llava_llama3_fp8_scaled.safetensors (8.5G)
│   │   └── clip_l.safetensors                   (235M)
│   └── vae/hunyuan_video_vae_bf16.safetensors   (471M)
├── SDXL-ComfyUI/
│   └── checkpoints/sd_xl_base_1.0.safetensors   (6.5G)
├── FLUX-ComfyUI/
│   ├── diffusion_models/flux1-dev-fp8.safetensors (12G)
│   ├── text_encoders/t5xxl_fp8_e4m3fn.safetensors (4.6G)
│   └── vae/ae.safetensors                         (320M)
└── TripoSplat-ComfyUI/
    ├── diffusion_models/triposplat_fp16.safetensors      (707M)
    ├── clip_vision/dino_v3_vit_h.safetensors             (1.6G)
    ├── vae/
    │   ├── triposplat_vae_decoder_fp16.safetensors       (550M)
    │   └── flux2-vae.safetensors                         (321M)
    └── background_removal/birefnet.safetensors           (424M)
```

---

## 2. 音频模型

| 模型 | 能力 | 端口 | GPU | 显存 | 大小 | 状态 |
|------|------|------|------|------|------|------|
| **CosyVoice 2 (0.5B)** | TTS + zero-shot 音色克隆 | 9880 | GPU2 | 2.5G | 4.4G | 运行中 |
| **faster-whisper large-v3-turbo** | STT 语音转文字 | 内嵌 | 共享 | 1.5G | 1.6G | 按需（`--stt`） |
| **edge-tts** | 在线 TTS（微软神经语音） | 9880 | CPU | 0 | 0（在线） | 运行中 |

### 用途说明

- **CosyVoice 2**：本地 TTS 引擎，支持 zero-shot 音色克隆。auto/quick 模式配音主力（`cosy-*` 前缀音色）。与 Wan 共享 GPU2（串行不抢资源）。
- **faster-whisper large-v3-turbo**：STT 语音转文字，int8_float16 量化。`--stt` 开启时对成片音频转写生成 SRT 字幕（覆盖 TTS 时间戳的兜底方案）。不需独立服务，运行时内嵌加载。
- **edge-tts**：微软在线 TTS，自然音色，不占 GPU。auto/quick 模式配音主力（`edge-*` 前缀音色，8 种内置音色）。
- **custom 模式用 H3 原生音频**，不使用 TTS/STT。

### 模型文件路径

```
/mnt/dataset/zxy/CosyVoice2-0.5B/
├── llm.pt                              (1.9G)
├── flow.pt                             (430M)
├── flow.cache.pt                       (430M)
├── hift.pt                             (80M)
├── speech_tokenizer_v2.onnx            (474M)
├── speech_tokenizer_v2.batch.onnx      (474M)
├── flow.decoder.estimator.fp32.onnx    (274M)
├── flow.encoder.fp32.zip               (184M)
├── flow.encoder.fp16.zip               (112M)
├── campplus.onnx                       (27M)
└── cosyvoice2.yaml

/mnt/dataset/zxy/hf_cache/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/
└── snapshots/.../model.bin             (1.6G, int8_float16)
```

---

## 3. 插帧模型

| 模型 | 能力 | 位置 | GPU | 大小 | 状态 |
|------|------|------|------|------|------|
| **RIFE v4.26** | 光流插帧（镜头间过渡） | ComfyUI `models/frame_interpolation/` | GPU2 | 11M | 运行中 |
| **RIFE v4.26 heavy** | 光流插帧（高质量变体） | 同上 | GPU2 | 11M | 备用 |

### 用途说明

- **RIFE v4.26**：auto/custom 模式镜头间光流过渡。提取相邻镜头首尾帧 → RIFE 插帧（multiplier=8）→ 0.375s@24fps 过渡片段。config `rife.enabled` 控制，`--no-rife` 禁用。
- `rife_v4.26_heavy` 是高质量变体，目前未使用。
- `rife.py` 中 `slowmo()` 单镜慢动作方法已实现但未接入流水线（v2.1 backlog）。

```
/mnt/disk_sdb/zxy/ComfyUI/models/frame_interpolation/
├── rife_v4.26.safetensors       (11M)
└── rife_v4.26_heavy.safetensors (11M)
```

---

## 4. LLM 模型（远程 API，不本地部署）

| 模型 | 用途 | 延迟 | 状态 |
|------|------|------|------|
| **deepseek-v4-flash** | 编剧、prompt 优化、LUT 风格选择、BGM mood 选择 | 快 | ✅ 免费 |
| **claude-haiku-4-5** | 多模态审片（逐镜抽帧读图打分） | 4s/镜 | ✅ 免费 |
| **claude-sonnet-4-6** | 严格审片备选（review_strict） | 534s/镜（太慢） | ✅ 免费 |

> USTC API：`https://api.llm.ustc.edu.cn/v1`，key 存 `config/config.json`（不入 git）

---

## 5. 程序生成资产（非模型）

| 资产 | 位置 | 大小 | 说明 |
|------|------|------|------|
| 3D LUT ×6 | `utils/luts/` | 948K×6 | cinematic/warm/cool/vintage/vivid/soft（`gen_luts.py` numpy 生成） |
| BGM | 运行时生成 | 0 | numpy 合成 5 种 mood（`music.py`），留 `bgm/` 自定义目录放真实素材 |

---

## 6. 已下载未使用

| 模型 | 位置 | 大小 | 说明 |
|------|------|------|------|
| Qwen3.6-35B-A3B | `/mnt/dataset/zxy/Qwen3.6-35B-A3B/` | 67G | 26 个 safetensors 分片，未接入任何流程 |
| MiniMax-H3 原始权重 | `/mnt/dataset/zxy/MiniMax-H3/` | 465G | 已转 int8 ComfyUI 格式（70G 在 `MiniMax-H3-ComfyUI/`），原始可删 |

---

## 7. GPU 与端口分配

| GPU | 显存 | 实例 | 端口 | 状态 |
|-----|------|------|------|------|
| GPU0 | 48G | MiniMax-H3 ref2va | 8188 | 运行中（custom 主力，46G） |
| GPU1 | 48G | SDXL Base 1.0 | 8191 | 运行中（7G + Isaac Sim 4.4G + aluupy 训练） |
| GPU2 | 48G | Wan2.2-5B + CosyVoice 2 + RIFE | 8189 / 9880 | 运行中（18G + 2.5G + aluupy 训练） |
| GPU3 | 48G | HunyuanVideo 13B | 8190 | 运行中（20G + aluupy 训练） |

- **FLUX.1-dev fp8 (8192) 已停**：auto 模式 `--character-mode flux/3dgs/auto` 需先重启 8192（TripoSplat 随 FLUX 共实例）
- faster-whisper STT 按需加载，共享 GPU 显存（不独立常驻）
- aluupy 在 GPU1/2/3 有训练任务，不能动

---

## 8. 各子命令与服务依赖

| 子命令 | 需要的服务 | 端口 |
|--------|-----------|------|
| `auto`（无角色） | Wan T2V + TTS | 8189 + 9880 |
| `auto --character-mode flux` | Wan I2V + FLUX + TTS | 8189 + 8192 + 9880 |
| `auto --character-mode 3dgs/auto` | Wan I2V + FLUX + TripoSplat + TTS | 8189 + 8192 + 9880 |
| `custom` | H3 ref2va | 8188 |
| `quick` | Wan T2V + TTS | 8189 + 9880 |
| 任意 `--stt` | + faster-whisper（GPU） | 内嵌 |

---

## 9. 部署记录

各模型部署细节见 `/mnt/disk_sdb/zxy/` 下：

| 文件 | 内容 |
|------|------|
| `MiniMax-H3-部署记录.md` | H3 ref2va/fl2va + Qwen3-VL-32B + 双 VAE，int8 量化转换 |
| `Wan2.2-部署记录.md` | Wan2.2-TI2V-5B + UMT5-XXL + VAE |
| `HunyuanVideo-部署记录.md` | HunyuanVideo 13B + LLaVA-Llama3 + CLIP-L + VAE |
| `SDXL-部署记录.md` | SDXL Base 1.0 |
| `FLUX-部署记录.md` | FLUX.1-dev fp8 + T5-XXL + VAE（含 TripoSplat 共实例配置） |

> 模型文件统一存 `/mnt/dataset/zxy/<Model>-ComfyUI/`（mergerFS 机械盘），通过 `extra_model_paths.yaml` 映射到 ComfyUI。
