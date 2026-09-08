# Vidance v1 设计文档 — 角色一致性

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v0 实现见 [v0-design.md](./v0-design.md)
>
> **状态：✅ 已完成**（2026-09-08 端到端验证通过，M1-M8 全部完成，I2V bug 已修复，v1.1 角色模式优化完成，3DGS PCA 对齐+接地合成位姿修复完成）
>
> **验证结果**：
> - v1 I2V 修复：概念"一只猫在月球上跳舞"+ 角色"穿宇航服的白猫" → 10.4s 成片（4 镜），I2V 首帧与 composite_ref 相关性 0.977-0.991（Wan22ImageToVideoLatent）。
> - v1.1 flux 模式：4 镜 16s 成片，I2V 相关性 0.993-0.998，审片全 7 分一次过（比 3DGS 模式重试少、质量更稳定）。
> - v1.1 auto 模式：3DGS 审查超时(score=5) → 自动降级 flux → 3 镜 8.9s 成片，降级逻辑验证通过。
> - 3DGS 重建质量是当前瓶颈（一致性 2-4/10），v1.1 通过 flux 模式绕过；3DGS 模式经 PCA 对齐+接地合成后 review_character 通过（score=7）；v2 探索多图 3D 重建 / IP-Adapter。

## 1. 概述

### 1.1 v0 遗留问题

v0 每镜独立 T2V 生成，同一角色跨镜头会**漂移**（外观/服装/体型不一致）——这是纯 T2V 的根本限制：文本 prompt 无法精确锁定视觉特征。

### 1.2 v1 目标

引入 **3DGS 角色锚**：全片唯一 3D 高斯泼溅表征，每镜头起始帧由 RenderSplat 从该 3DGS 按镜头角度渲染，喂 Wan I2V 生成，实现角色跨镜头一致。

- **输入**：一句中文概念 + 角色描述
- **输出**：10-30 秒有声短片，同一角色在所有镜头中外观一致
- **核心验证**：3DGS 能否作为可靠的跨镜头角色锚

### 1.3 v1 范围

| 做 | 不做（留给后续版本） |
|----|---------------------|
| FLUX 生成角色参考图 | 多角色同场（v2+） |
| TripoSplat 单图→3DGS | 角色动作驱动（v3，3D 动画） |
| RenderSplat 按角度渲染参考帧 | 长视频（v2，仍 ≤5s/镜） |
| RenderSplat bg_image 合成角色+背景 | 配乐/调色（v2） |
| Wan 5B I2V 生成（复用现有 ti2v 模型） | 14B 高质量 I2V（可选增强） |
| 角色一致性审片维度 | STT 字幕（v2+） |
| 自定义 I2V workflow 模板 | 光流插帧软过渡（v2） |

### 1.4 核心验证点

1. TripoSplat 单图→3DGS 质量是否够用（高斯数、表面完整性）
2. RenderSplat 渲染视角变化时角色是否稳定（不崩坏）
3. RenderSplat bg_image 合成能否自然融入 FLUX 生成的背景
4. Wan 5B I2V 能否基于合成参考帧生成角色一致的视频
5. 全链路显存是否够（TripoSplat + FLUX + Wan 同卡或分卡）

---

## 2. 架构设计

### 2.1 v0 → v1 架构演进

```
v0:  concept → LLM编剧 → [每镜 T2V] → TTS → 审片 → 合成
                                     ↑ 独立生成，角色漂移

v1:  concept → LLM编剧 → [角色: FLUX生图 → TripoSplat→3DGS]  ← 角色锚，全片唯一
                         → [每镜: RenderSplat(角度+背景) → 合成参考帧 → Wan I2V] → TTS → 审片 → 合成
                                                                    ↑ 角色锁定
```

### 2.2 三层架构（v1）

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v1: 角色一致性短片                              │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(reviewer) + skills         │
│           core/ pipeline（角色锚阶段 + 逐镜阶段）          │
├──────────────────────────────────────────────────────────┤
│  引擎层   角色: FLUX(8192) → TripoSplat → RenderSplat      │
│           视频: Wan I2V (8189, 复用 5B ti2v)               │
│           音频: edge-tts + CosyVoice (9880)                │
│           LLM: USTC API    后处理: ffmpeg + moviepy        │
└──────────────────────────────────────────────────────────┘
```

### 2.3 混合模式（v1 扩展）

v0 的分工原则不变，新增角色锚阶段的归属：

| 能力 | 类型 | 归属 |
|------|------|------|
| 概念→分镜脚本（含角色描述+每镜 camera 角度） | 创意决策 | **agent**（director + LLM） |
| 角色描述→英文 character_prompt | 创意决策 | **agent** |
| 每镜 scene_desc→英文 video_prompt + camera 角度 | 创意决策 | **agent** |
| FLUX 生角色参考图 | 确定性执行 | **code** |
| TripoSplat 单图→3DGS | 确定性执行 | **code**（新 workflow） |
| RenderSplat 渲染参考帧 | 确定性执行 | **code**（新 workflow） |
| Wan I2V 生成视频 | 确定性执行 | **code**（新 workflow） |
| TTS 配音 | 确定性执行 | **code**（复用 v0） |
| 审片打分（含角色一致性维度） | 多模态判断 | **agent**（reviewer） |
| 拼接合成 | 确定性执行 | **code**（复用 v0） |

### 2.4 agent 拓扑（v1）

```
director (主控 agent)
  │
  ├─ [LLM] 编剧：concept + character_desc → script（含每镜 camera: {yaw,pitch,fov}）
  ├─ [LLM] 角色描述 → 英文 character_prompt
  ├─ [LLM] 每镜 scene_desc → video_prompt + camera 参数
  │
  ├─ [角色锚阶段] (全片一次)
  │   ├─ [FLUX 8192] character_prompt → character_ref.png
  │   ├─ [TripoSplat] character_ref.png → character.splat (3DGS)
  │   └─ [RenderSplat 预览] 3DGS → 多角度预览 → 审查角色质量
  │
  ├─ [逐镜阶段] (每镜)
  │   ├─ [FLUX 8192] scene_desc → background.png (无角色背景)
  │   ├─ [RenderSplat] 3DGS + camera + bg_image → composite_ref.png
  │   ├─ [Wan I2V 8189] composite_ref + video_prompt → shot_{id}.mp4
  │   ├─ [TTS 9880] narration → shot_{id}.wav
  │   ├─ [抽帧] → shot_{id}_frame_*.jpg
  │   └─ task → reviewer 审片（含角色一致性维度）
  │
  └─ [合成] ffmpeg_tools.compose() → final.mp4
```

### 2.5 同步执行

沿用 v0 的同步阻塞模式。角色锚阶段在逐镜阶段前一次性完成。无异步队列。

---

## 3. 核心流程：3DGS 角色锚

### 3.1 角色锚阶段（全片一次）

```
[1] LLM 生成角色描述
      输入: concept + 用户可选的角色设定
      输出: character_desc（中文，如"穿红色斗篷的少年，短发，背对着镜头"）

[2] LLM 翻译/优化 → character_prompt
      输出: 英文 FLUX prompt，含角色外观细节，正面清晰构图，纯色背景
      约束: "single character, full body, front view, plain background, 
             high detail, character sheet reference"

[3] FLUX 生成角色参考图
      POST 8192 /prompt (flux workflow)
      → character_ref.png (1024×1024 或 1280×704)
      约束: 单角色、前景清晰、便于去背

[4] TripoSplat 单图 → 3DGS
      POST 8192 /prompt (triposplat workflow)
      输入: character_ref.png
      内部: BiRefNet 去背 → Preprocess(1024²) → Conditioning(DINOv3+Flux2VAE) 
            → KSampler(20步) → VAEDecodeTripoSplat → SPLAT
      → character.splat (3DGS, 默认 262144 高斯)

[5] RenderSplat 多角度预览审查
      渲染 4 个角度（front/side/back/3-quarter）→ preview_*.png
      → reviewer 审查 3DGS 质量（角色是否完整、无残缺）
      → 不通过则回到 [3] 重新生成参考图（≤2 次）
```

### 3.2 逐镜阶段（每镜）

```
[6] FLUX 生成背景
      输入: scene_desc（不含角色）→ 英文 background_prompt
      约束: "empty scene background, no character, 
             {scene description}, cinematic"
      → background_{id}.png

[7] RenderSplat 合成参考帧
      输入: character.splat + camera_info(yaw,pitch,fov) + bg_image=background_{id}.png
      内部: 按镜头角度渲染 3DGS → alpha 混合到背景图
      → composite_ref_{id}.png (角色锁定在该镜头视角 + 场景背景)

[8] Wan I2V 生成视频
      POST 8189 /prompt (wan_i2v workflow)
      输入: start_image=composite_ref_{id}.png, video_prompt
      内部: Wan22ImageToVideoLatent(首帧VAE编码→latent+noise_mask) → KSampler(inpainting) → VAEDecode → 视频
      → shot_{id}.mp4 (角色从参考帧姿态开始运动)

[9] TTS + 抽帧 + 审片 (复用 v0)
      审片增加 character_consistency 维度（与角色参考图对比）
```

### 3.3 角色一致性的保障机制

| 环节 | 机制 |
|------|------|
| 3DGS 唯一性 | 全片只建一次 3DGS，所有镜头共用 |
| 视角一致性 | RenderSplat 用 CreateCameraInfo 精确控制 yaw/pitch/fov |
| 首帧锁定 | Wan I2V 以合成参考帧为首帧，角色姿态从该帧开始演化 |
| 审片校验 | reviewer 对比角色参考图，打 character_consistency 分 |

---

## 4. 数据模型（v1 扩展）

### 4.1 分镜脚本 schema（v1 新增字段）

```json
{
  "title": "月球上的猫",
  "concept": "一只猫在月球上跳舞",
  "style": "cinematic, warm sunset tone, 35mm film grain",
  "character": {
    "desc": "一只穿着宇航服的白猫，毛色纯白，大眼睛",
    "prompt": "a white cat wearing a tiny spacesuit, pure white fur, big eyes, full body, front view, plain background, high detail, character reference sheet",
    "ref_image": "character/character_ref.png",
    "splat": "character/character.splat"
  },
  "shots": [
    {
      "id": 1,
      "scene_desc": "远景：荒凉的月球表面，白猫缓缓走入画面",
      "narration": "在寂静的月海，我独自起舞",
      "video_prompt": "Wide shot, lunar surface, slow camera pan, cinematic",
      "background_prompt": "desolate lunar surface, gray dust, Earth in black sky, no character, cinematic, 35mm",
      "camera": {
        "yaw": 35,
        "pitch": 10,
        "fov": 50,
        "distance": 4.5
      },
      "duration": 5,
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

v1 新增：
- `character`：角色锚信息（desc/prompt/ref_image/splat 路径）
- `shots[].background_prompt`：无角色背景的生成 prompt
- `shots[].camera`：RenderSplat 相机参数（yaw/pitch/fov/distance）

### 4.2 审片结果 schema（v1 新增维度）

```json
{
  "shot_id": 1,
  "score": 8,
  "dimensions": {
    "consistency": 8,
    "quality": 9,
    "motion": 7,
    "artifact": 8,
    "character_consistency": 8
  },
  "feedback": "...",
  "pass": true
}
```

新增 `character_consistency` 维度：生成视频中的角色与 character_ref.png 的一致性（外观/服装/毛色）。

### 4.3 任务元数据 schema（v1 扩展）

```json
{
  "task_id": "20260908_001",
  "concept": "...",
  "character": {
    "ref_image": "character/character_ref.png",
    "splat": "character/character.splat",
    "preview_angles": ["front.png", "side.png", "back.png", "3quarter.png"],
    "review": {"score": 8, "pass": true}
  },
  "clips": [
    {
      "shot_id": 1,
      "background": "clips/background_1.png",
      "composite_ref": "clips/composite_ref_1.png",
      "camera": {"yaw": 35, "pitch": 10, "fov": 50},
      "attempts": [...],
      "final_video": "clips/shot_1.mp4",
      "audio": {...}
    }
  ]
}
```

---

## 5. 引擎接口规格

### 5.1 FLUX T2I（8192，复用现有）

角色参考图 + 每镜背景图都用 FLUX。复用 v0 已部署的 FLUX 服务。

```
角色图: character_prompt → character_ref.png (1024×1024, 纯色背景便于去背)
背景图: background_prompt → background_{id}.png (1280×704, 无角色)
```

### 5.2 TripoSplat（ComfyUI 8192，新 workflow）

单图→3DGS。4 个核心节点：

| class_type | 作用 | 关键参数 |
|-----------|------|---------|
| `TripoSplatPreprocessImage` | 去背+预处理 | image, mask, erode_radius=1, size=1024 |
| `TripoSplatConditioning` | 编码条件 | clip_vision(DINOv3), vae(Flux2), image → positive/negative/latent |
| `VAEDecodeTripoSplat` | latent→3DGS | samples, vae(TripoSplat decoder), num_gaussians=262144 |
| `TripoSplatSamplingPreview` | 预览patch（可选） | yaw=90, pitch=15 |

完整拓扑（`utils/workflows/triposplat.json`）：
```
LoadImage → [BiRefNet 去背] → TripoSplatPreprocessImage → TripoSplatConditioning
                                                                    ↓
UNETLoader(triposplat_fp16) → KSampler(20步,cfg=3) → VAEDecodeTripoSplat → SPLAT
                                            ↑
                              CLIPVisionLoader(dino_v3) + VAELoader(flux2-vae)
```

模型文件（5 个，需新下载到 `/mnt/dataset/zxy/TripoSplat-ComfyUI/`）：

| 文件 | 目录 | 大小(估) | 来源 |
|------|------|---------|------|
| triposplat_fp16.safetensors | diffusion_models/ | ~6G | hf VAST-AI/TripoSplat |
| dino_v3_vit_h.safetensors | clip_vision/ | ~2.5G | hf VAST-AI/TripoSplat |
| triposplat_vae_decoder_fp16.safetensors | vae/ | ~500M | hf VAST-AI/TripoSplat |
| flux2-vae.safetensors | vae/ | ~300M | hf VAST-AI/TripoSplat |
| birefnet.safetensors | background_removal/ | ~900M | hf Comfy-Org/BiRefNet |

需在 `extra_model_paths.yaml` 新增 `triposplat` 段映射这些子目录。

### 5.3 RenderSplat（ComfyUI 8192，新 workflow）

3DGS→指定视角图像 + 背景合成。核心节点：

| class_type | 作用 | 关键参数 |
|-----------|------|---------|
| `RenderSplat` | 渲染 SPLAT→IMAGE | splat, width, height, camera_info, **bg_image**(可选), render_style=color |
| `CreateCameraInfo` | 相机参数 | mode=orbit, yaw, pitch, distance, fov, roll |

合成能力（v1 关键）：`RenderSplat(splat=角色3DGS, camera_info=镜头角度, bg_image=FLUX背景)` → 角色按该角度渲染后 alpha 混合到背景图 → 合成参考帧。

拓扑（`utils/workflows/rendersplat.json`）：
```
File3DToSplat(character.splat) → RenderSplat ← CreateCameraInfo(yaw,pitch,fov)
                                              ← LoadImage(background_{id}.png) [bg_image]
                            → composite_ref.png
```

### 5.4 Wan I2V（ComfyUI 8189，新 workflow）

图生视频。**复用现有 5B ti2v 模型**（ti2v = text+image to video，已支持 I2V）。

关键节点：`Wan22ImageToVideoLatent`（Wan 2.2 专用 I2V 节点，48ch latent + noise_mask inpainting）

> **I2V 节点选择**（v1 修复）：最初使用 `WanImageToVideo`（Wan 2.1 节点，16ch latent + concat_cond），但 Wan 2.2 ti2v 模型 latent_channels=48 且 in_dim=48 → extra_channels=0 → concat_cond 返回 None → I2V 图片被完全忽略（首帧与输入相关性 -0.26）。改用 `Wan22ImageToVideoLatent` 后，首帧与 composite_ref 相关性 0.977-0.991。

| 维度 | T2V (v0) | I2V (v1) |
|------|---------|---------|
| latent 节点 | `Wan22ImageToVideoLatent`（空 latent） | `Wan22ImageToVideoLatent`（start_image 注入） |
| start_image | 无 | **有**（VAE 编码→首帧 latent + noise_mask=0） |
| 输出 | LATENT（含 noise_mask） | LATENT（含 noise_mask） |
| 机制 | 纯 T2V | inpainting 式（首帧保留，其余帧去噪） |
| 模型 | wan2.2_ti2v_5B（复用） | wan2.2_ti2v_5B（复用） |
| VAE | wan2.2_vae（复用） | wan2.2_vae（复用） |
| 空间压缩 | 16x（Wan 2.2 VAE） | 16x（Wan 2.2 VAE） |

拓扑（`utils/workflows/wan_i2v.json`）：
```
LoadImage(composite_ref) → Wan22ImageToVideoLatent(vae, start_image) → LATENT
CLIPTextEncode(prompt) ──────────────────────────→ KSampler(positive)
CLIPTextEncode(negative) ────────────────────────→ KSampler(negative)
                                                     ↓
UNETLoader(ti2v_5B) → ModelSamplingSD3 → KSampler(seed,steps,cfg,latent) → VAEDecode → CreateVideo → VIDEO
VAELoader(wan2.2_vae) ────────────────────────────↑
CLIPLoader(umt5_xxl) → CLIPTextEncode ×2
```

> **注意**：5B ti2v 的 I2V 路径用单模型单 KSampler（不像 14B 蓝图用双模型两段采样）。VAE 复用 wan2.2_vae（非 14B 蓝图的 wan_2.1_vae）。

### 5.5 TTS / ffmpeg / LLM（复用 v0）

TTS 双引擎、ffmpeg 合成、LLM 审片均复用 v0，无需改动。

---

## 6. ComfyUI 上传图片支持

I2V 需要把本地生成的图片传给 ComfyUI。`utils/comfy_api.py` 需新增：

```python
def upload_image(self, image_path: str) -> str:
    """上传图片到 ComfyUI input/ 目录，返回文件名"""
    # POST /upload/image with multipart/form-data
    # ComfyUI 会存到 input/ 目录，返回 {name, subfolder}
```

I2V workflow 中 `LoadImage` 节点的 `image` 参数用上传返回的文件名。

---

## 7. GPU 与显存规划

### 7.1 v1 GPU 分配

| GPU | 服务 | 显存占用(估) | 说明 |
|-----|------|------------|------|
| GPU0 | FLUX (8192) | 36G | 角色图+背景图生成 |
| GPU1 | SDXL (8191) | 31.5G | 备选（v1 可能不用） |
| GPU2 | Wan I2V (8189) + TTS (9880) | 17.8G + 2.5G | 视频+配音 |
| GPU3 | HunyuanVideo (8190) | 20.1G | 备选 |

**TripoSplat 放哪？** 选项：
- **方案A（推荐）**：放 GPU0 与 FLUX 共享（FLUX 用完释放，TripoSplat 接力），需串行
- **方案B**：放 GPU1（SDXL 让位或共享），TripoSplat 显存估 ~15-20G
- 待实测 TripoSplat 实际显存后定

### 7.2 TripoSplat 显存估算

- triposplat_fp16 模型 ~6G（fp16）
- DINOv3 ViT-H ~2.5G
- Flux2 VAE + TripoSplat VAE decoder ~1G
- KSampler 中间张量（8192×16 latent）+ 高斯解码（262144 高斯）~5-10G
- 估计峰值 ~15-20G，4090 48G 充裕

---

## 8. Workflow 模板清单（v1 新增）

| 模板 | 用途 | 节点数 |
|------|------|--------|
| `workflows/triposplat.json` | 单图→3DGS | ~14（含BiRefNet去背） |
| `workflows/rendersplat.json` | 3DGS+角度+背景→合成图 | ~5 |
| `workflows/wan_i2v.json` | 合成图→视频 | ~11 |
| `workflows/flux_t2i.json` | FLUX 文生图（角色/背景） | ~8 |

沿用 v0 的 `{{var}}` 占位符模板渲染机制。

---

## 9. 审片闭环（v1 扩展）

### 9.1 打分标准（新增维度）

5 维度（v0 的 4 维 + character_consistency）：

| 维度 | 标准 | 低分表现 |
|------|------|---------|
| consistency | 画面与 scene_desc 匹配 | 场景/动作与描述不符 |
| quality | 清晰度/色彩/构图 | 模糊/过曝/构图差 |
| motion | 运动自然度 | 静止/卡顿/僵硬 |
| artifact | 无 AI 痕迹 | 畸形/融合/闪烁 |
| **character_consistency** | 角色与 character_ref 一致 | 毛色/服装/体型漂移 |

综合分 = 五维均值。**≥7 通过**。

### 9.2 角色锚审查（新增）

角色锚阶段完成后，渲染 4 角度预览给 reviewer 审查：
- 角色是否完整（无残缺/悬浮高斯）
- 多角度是否稳定（不崩坏）
- 不通过则重新生成参考图（≤2 次）

### 9.3 重试策略

沿用 v0：最多 3 次尝试，最高分兜底。v1 新增：角色一致性过低（<5）时，优先重试 RenderSplat 合成而非只换 seed。

---

## 10. 依赖与前置

### 10.1 新增模型下载

| 模型 | 用途 | 来源 | 状态 |
|------|------|------|------|
| TripoSplat 5 文件 | 单图→3DGS | hf-mirror（VAST-AI/TripoSplat） | ❌ 待下载 |

> HuggingFace 被墙，用 hf-mirror.com 镜像下载。umt5/wan2.2_vae/ti2v_5B 已有，复用。

### 10.2 新增 ComfyUI 配置

`extra_model_paths.yaml` 新增 `triposplat` 段：
```yaml
triposplat:
    base_path: /mnt/dataset/zxy/TripoSplat-ComfyUI/
    diffusion_models: diffusion_models/
    clip_vision: clip_vision/
    vae: vae/
    background_removal: background_removal/
```

### 10.3 代码新增

| 文件 | 改动 |
|------|------|
| `utils/comfy_api.py` | 新增 `upload_image()`、`generate_i2v()`、`generate_flux_t2i()` |
| `utils/workflows/triposplat.json` | 新建 |
| `utils/workflows/rendersplat.json` | 新建 |
| `utils/workflows/wan_i2v.json` | 新建 |
| `utils/workflows/flux_t2i.json` | 新建 |
| `core/pipeline.py` | 新增角色锚阶段 + 逐镜改 I2V 路径 |
| `utils/llm.py` | 编剧 prompt 增加角色描述 + camera 参数输出 |
| `.opencode/skills/review/SKILL.md` | 审片标准加 character_consistency 维度 |

### 10.4 复用 v0 组件

TTS 双引擎、ffmpeg 合成、LLM 客户端、审片 agent、opencode 配置 —— 全部复用。

---

## 11. 验收标准

v1 跑通的标志：

1. **角色锚**：FLUX 生图 → TripoSplat 出 3DGS → 4 角度预览审查通过
2. **角色一致**：成片所有镜头中同一角色外观一致（character_consistency ≥7）
3. **I2V 生成**：每镜以 RenderSplat 合成参考帧为首帧，Wan I2V 生成视频
4. **背景合成**：角色自然融入 FLUX 生成的场景背景
5. **端到端**：概念 → 有声成片，无需人工干预
6. **元数据**：meta.json 记录角色锚信息 + 每镜 camera 参数 + 合成参考帧路径

验收命令（设计）：
```bash
python core/pipeline.py "一个穿红斗篷的少年在雪原上行走" --character "穿红色斗篷的短发少年"
```

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| TripoSplat 单图 3DGS 质量不足 | 角色锚不可用 | 评估高斯数/表面质量；备选：多视图输入（若支持） |
| RenderSplat 视角变化角色崩坏 | 某些角度不可用 | 限制 camera 角度范围；审片拦截 |
| bg_image 合成不自然（光照/透视不匹配） | 参考帧违和 | FLUX 背景与角色光照风格对齐；RenderSplat headlight_shading 调整 |
| Wan 5B I2V 质量不如 14B | 视频质量下降 | 先用 5B 验证流程；质量不够再下 14B（~28G） |
| TripoSplat 显存超预期 | OOM | 实测后定 GPU 放置；降 num_gaussians |
| HF 下载 TripoSplat 被墙 | 模型拿不到 | 用 hf-mirror.com 或 modelscope 镜像 |
| 角色参考图去背不干净 | 3DGS 有背景残留 | BiRefNet 去背 + erode_radius 调整 |
| 全链路耗时过长 | 单片生成慢 | 角色锚阶段一次性；逐镜可并行（v2 引入异步） |

---

## 13. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | TripoSplat 模型下载 + extra_model_paths 配置 | 模型就位 |
| M2 | triposplat.json workflow + 单图→3DGS 验证 | character.splat |
| M3 | rendersplat.json + 多角度渲染 + bg_image 合成验证 | composite_ref.png |
| M4 | wan_i2v.json + 5B I2V 验证 | shot.mp4 |
| M5 | comfy_api 新增 upload_image/generate_i2v/generate_flux_t2i | 代码就绪 |
| M6 | pipeline 角色锚阶段 + 逐镜 I2V 改造 | 端到端 |
| M7 | 审片加 character_consistency + 联调 | 闭环 |
| M8 | 端到端验证 + 角色一致性评估 | v1 成片 |

> 建议从 M1 开始按里程碑推进，每个里程碑独立可验证。

---

## 14. v1.1 角色模式优化（已完成）

### 14.1 背景与问题

v1 端到端验证发现 3DGS 角色重建是核心瓶颈：
- TripoSplat 单图重建质量不足（262K 高斯，像素覆盖 13-26%，颜色偏暗）
- 审查反馈"严重崩坏，破碎碎片状，姿态水平漂浮"
- I2V 忠实复现低质量参考帧（garbage-in-garbage-out），导致角色一致性 2-4/10
- **位姿问题**：TripoSplat 输出 Y-up 且不轴对齐（PCA 主轴偏离垂直 ~39°，Z-alignment=0.774），角色渲染后倾斜/侧卧/悬浮；旧合成方式角色居中占画面 78-85%，悬浮感强

### 14.2 优化措施

| 措施 | 说明 |
|------|------|
| **I2V 节点修复** | `WanImageToVideo`（16ch concat_cond，被 ti2v 忽略）→ `Wan22ImageToVideoLatent`（48ch + noise_mask inpainting），首帧相关性 0.99+ |
| **character-mode 开关** | `auto`（默认，3DGS 审查不过自动降级 flux）/ `3dgs` / `flux` |
| **flux 模式** | 跳过 3D 重建，每镜 FLUX 直接生成角色+场景完整图 → Wan I2V（质量最稳定） |
| **前置镜头 FLUX 替代** | 3dgs 模式下 |yaw|<30 的镜头用 FLUX 生成完整场景图，不走 3DGS 渲染 |
| **位姿修复（prompt）** | FLUX 角色 prompt 强调 "standing upright on the ground, natural pose"；review_character 加 pose 维度（4 维评分） |
| **PCA 自动对齐** | `splat_renderer.py:load_ply` 对点云做 PCA，最大方差轴旋转到 Z(垂直)，亮度检测确保头朝上（Z-alignment 0.774→1.000），替代手动 Y/Z 交换 |
| **接地合成** | `composite_bg` 裁剪角色 bbox → 缩放到 55% 画面高度 → 放到 88% 位置（脚踩地不悬浮），替代旧居中 paste（78-85% 占比悬浮） |
| **optimize_scene_prompt** | 新增 LLM 方法，角色+场景组合 FLUX prompt（角色自然融入场景，站立姿态） |

### 14.3 验证结果

| 模式 | 镜数 | 时长 | I2V 相关性 | 审片 | 重试 | review_character | 说明 |
|------|------|------|-----------|------|------|-----------------|------|
| flux | 4 | 16s | 0.993-0.998 | 全 7 分 | shot4×1 | — | 每镜一次过，质量稳定 |
| auto | 3 | 8.9s | 0.994-0.997 | 7/7/8 | shot3×2 | score=5 超时 | 3DGS 审查超时→自动降级 flux |
| 3dgs（PCA+接地） | 3 | 7.2s | 0.986-0.998 | 全 7 分 | shot1×3 | **score=7 通过** | PCA 对齐+接地合成后 review_character 首次通过 |

- 输出目录：`output/20260908_163052/`（flux）、`output/20260908_164931/`（auto）、`output/20260908_203615/`（3dgs PCA+接地）
- flux 模式比 3dgs 模式重试少（3dgs 每镜重试 2-3 次 vs flux 多数一次过）
- 3dgs 模式 review_character 进展：score=2（原版）→ score=4（Y/Z交换）→ **score=7 通过**（PCA 对齐+接地合成）

### 14.4 代码改动

- `core/pipeline.py`：`run()` 加 `character_mode` 参数；`_build_character_anchor` 支持 flux 模式（跳过 3D 重建）；`_process_shot` 按 mode + yaw 分流参考帧
- `utils/llm.py`：`optimize_character_prompt` 强调站立；新增 `optimize_scene_prompt`；`review_character` 加 pose 维度
- `utils/splat_renderer.py`：`load_ply` 加 `_align_upright()` PCA 对齐（eigh 特征向量→最大方差映射 Z，亮度检测头朝上）；`composite_bg` 重写为 crop bbox → scale 55% → ground 88%
- CLI：`--character-mode auto|3dgs|flux`

### 14.5 后续（v2）

- 多图 3D 重建（多视角输入提升 mesh 质量）
- 参考图 ControlNet / IP-Adapter 角色锁定
- flux 模式侧面镜头角色走样问题（无 3D 约束）
- 表面平滑优化（增大 min_px/max_px 或提高渲染分辨率，减少 3DGS 稀疏噪声）
