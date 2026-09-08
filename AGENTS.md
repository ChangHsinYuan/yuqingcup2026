# Vidance — AI 有声短片生成

> 本文件供 opencode agent 读取，了解项目约定与命令。

## 项目概述

Vidance 是一个基于 opencode agent 编排的本地视频生成系统。核心流程：中文概念→LLM编剧→Wan T2V生成→双引擎TTS配音→多模态审片→ffmpeg合成→有声短片。

**当前状态：v1.1 角色一致性优化完成**（2026-09-08），详见 [docs/v1-design.md](./docs/v1-design.md)。

v1 角色锚流程：FLUX 生角色图 → TripoSplat 重建 3DGS → 每镜 RenderSplat 按角度渲染参考帧 → Wan I2V 生成。

**v1.1 改进**：
- **I2V 修复**：`WanImageToVideo` → `Wan22ImageToVideoLatent`（Wan 2.2 原生 48ch latent + noise_mask inpainting），首帧与参考图相关性 0.99+
- **character-mode 开关**：`auto`（默认，3DGS 审查不过自动降级 flux）/ `3dgs` / `flux`（每镜 FLUX 直接生成角色+场景图，不重建 3D）
- **前置镜头用 FLUX 原图替代 3DGS**：3dgs 模式下 |yaw|<30 的镜头直接用 FLUX 生成角色+场景完整图，不走 3DGS 渲染
- **角色位姿修复**：FLUX 角色 prompt 强调 "standing upright on the ground"，审查加 pose 维度
- 不传 `--character` 时走 v0 纯 T2V

## 目录结构

```
vidance/
├── opencode.json              # opencode 配置（agents + permissions）
├── AGENTS.md                  # 本文件
├── config/config.json         # 运行时配置（API key、引擎地址、模型名、音色）
├── core/pipeline.py           # 端到端流水线主控
├── utils/
│   ├── comfy_api.py           # ComfyUI HTTP 客户端（T2V/I2V/TripoSplat/FLUX T2I）
│   ├── llm.py                 # USTC LLM 客户端（编剧/prompt优化/审片，含图片缩放+重试）
│   ├── tts.py                 # TTS 客户端（双引擎：edge-tts + CosyVoice）
│   ├── tts_server.py          # TTS FastAPI 服务（双引擎，GPU2:9880）
│   ├── splat_renderer.py      # 3DGS PLY 多角度渲染器（render_splat_at_angle + composite_bg）
│   ├── ffmpeg_tools.py        # 抽帧/SRT/合成
│   └── workflows/
│       ├── wan_t2v.json       # Wan T2V workflow 模板
│       ├── wan_i2v.json       # Wan I2V workflow 模板（12 节点，Wan22ImageToVideoLatent）
│       ├── triposplat.json    # TripoSplat 单图→PLY workflow（13 节点）
│       └── flux_t2i.json      # FLUX T2I workflow 模板（10 节点）
├── voices/                    # 自定义 CosyVoice 克隆音色素材目录
├── .opencode/
│   ├── agents/{director,reviewer}.md
│   └── skills/{scriptwriting,review}/SKILL.md
├── docs/                      # 设计文档（roadmap + v0-design + v1-design）
├── voice_samples/ → /mnt/dataset/...  # 音色试听样本（软链）
└── output/ → /mnt/dataset/... # 成片 + 元数据（软链到机械盘）
```

## 运行命令

### 全自动生成短片（v1.1 角色一致性）
```bash
# auto 模式（默认）：先试 3DGS，审查不过自动降级 flux
python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫" --voice edge-xiaoxiao

# flux 模式：每镜 FLUX 直接生成角色+场景图，不重建 3D（推荐，质量更稳定）
python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫" --character-mode flux --voice edge-xiaoxiao

# 3dgs 模式：强制 3DGS 角色锚（|yaw|<30 前置镜头仍用 FLUX 原图）
python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫" --character-mode 3dgs --voice edge-xiaoxiao
```

### v0 纯 T2V（无角色锚）
```bash
python core/pipeline.py "一只猫在月球上跳舞"
```

### 启动 TTS 服务
```bash
conda activate cosyvoice
CUDA_VISIBLE_DEVICES=2 TTS_FP16=1 python utils/tts_server.py &
```

### 单步操作
```bash
# 编剧
python utils/llm.py --concept "概念"

# T2V 生成
python utils/comfy_api.py "english prompt" -o output/clips/shot_1.mp4

# TTS 配音（列出所有音色）
python utils/tts.py --list-voices

# TTS 配音（指定音色，edge-* 或 cosy-*）
python utils/tts.py "中文旁白" -v edge-moe -o output/clips/shot_1.wav

# 抽帧
python utils/ffmpeg_tools.py frames output/clips/shot_1.mp4 -n 4
```

## TTS 音色规范

音色按前缀路由引擎（服务在 9880）：

| 前缀 | 引擎 | 示例 |
|------|------|------|
| `edge-*` | edge-tts（微软在线，自然） | `edge-xiaoxiao` 晓晓 / `edge-moe` 萌系高音 / `edge-family` 家人们风 |
| `cosy-*` | CosyVoice（本地 GPU，可克隆） | `cosy-default` / `cosy-cross` |
| `cosy-<自定义>` | CosyVoice 自定义克隆 | 在 `voices/<名>/prompt.wav` 放素材自动注册 |

试听样本：`vidance/voice_samples/`（17 个 wav）。默认音色 `edge-xiaoxiao`（config.json）。

## 服务端口

| 服务 | 端口 | GPU | 环境 |
|------|------|-----|------|
| Wan T2V/I2V (ComfyUI) | 8189 | GPU2 | comfyui |
| HunyuanVideo (ComfyUI) | 8190 | GPU3 | comfyui |
| SDXL (ComfyUI) | 8191 | GPU1 | comfyui |
| FLUX + TripoSplat (ComfyUI) | 8192 | GPU0 | comfyui |
| TTS 双引擎 (edge-tts + CosyVoice) | 9880 | GPU2 | cosyvoice |

> TripoSplat 与 FLUX 共用 GPU0:8192 同一 ComfyUI 实例（串行调用，不抢资源）。

## 关键约定

1. **随机 seed**：每次 T2V/I2V 生成必须用随机 seed（ComfyUI 缓存命中问题）
2. **每镜 ≤5s**：Wan 单次上限 121 帧≈5s@24fps
3. **旁白字数**：duration × 4 字（中文约 4 字/秒）
4. **审片阈值**：综合分 ≥7 通过，最多重试 2 次，取最高分兜底
5. **时长对齐**：成片按配音时长为准，画面不足定格末帧
6. **元数据完整**：每次任务记录 meta.json（脚本/prompt/seed/审片/时间戳）
7. **角色锚流程**（v1.1）：`--character` 传入角色描述 → FLUX 生角色参考图 → 按 `--character-mode` 分流：
   - `flux`：每镜 FLUX 直接生成角色+场景完整图 → Wan I2V（不重建 3D，质量最稳定）
   - `3dgs`：FLUX 图 → TripoSplat→3DGS → 每镜 RenderSplat 按角度渲染 → composite bg → Wan I2V（|yaw|<30 前置镜头用 FLUX 原图替代）
   - `auto`（默认）：先走 3DGS 流程 + review_character 审查，不过则自动降级 flux
8. **审片 5 维**（v1）：consistency/quality/motion/artifact/character_consistency，与角色参考图对比
9. **审片图片缩放**：上传前 resize 到 768px + JPEG 85%（原始 832×480 太大导致 API 超时）
10. **审片超时处理**：60s timeout + 1 retry，失败则 safe fallback auto-pass（不阻塞流水线）

## Python 环境

- **主环境 (comfyui)**：`/home/zxy/.conda/envs/comfyui/bin/python`（torch 2.13+cu130）
  - 用于：pipeline、comfy_api、llm、ffmpeg_tools、moviepy
- **TTS 环境 (cosyvoice)**：`/home/zxy/.conda/envs/cosyvoice/bin/python`（torch 2.3.1+cu121）
  - 用于：tts_server（CosyVoice 推理）

## LLM 模型

| 模型 | 用途 |
|------|------|
| deepseek-v4-flash | 编剧、prompt 优化 |
| claude-haiku-4-5 | 多模态审片（主力，4s/镜） |
| claude-sonnet-4-6 | 审片备选（review_strict，534s/镜太慢） |

## 已知限制（v1.1）

1. **3DGS 角色重建质量仍是瓶颈**（character_consistency 2-4/10）：
   - TripoSplat 262K 高斯渲染稀疏，像素覆盖仅 13-26%（512×512 渲染）
   - 3DGS 颜色偏暗（mean 0.51 vs FLUX 原图 0.94），宇航服细节丢失
   - 审查反馈：3D 重建"严重崩坏，破碎的碎片状"，角色姿态错误（水平漂浮而非站立）
   - **v1.1 缓解**：`flux` 模式跳过 3D 重建，每镜 FLUX 直接生成角色+场景图；`auto` 模式 3DGS 审查不过自动降级 flux
   - **v1.1 修复**：I2V 用 Wan22ImageToVideoLatent（48ch + noise_mask），首帧与参考图相关性 0.99+；位姿 prompt 强调站立
   - **后续**：v2 探索多图 3D 重建 / 参考图 ControlNet / IP-Adapter 角色锁定
2. **flux 模式角色一致性依赖 FLUX prompt**：侧面/背面镜头（yaw≠0）FLUX 生成角色可能走样，无 3D 约束
3. **多模态审片 API 延迟波动大**（12s-200s+）：
   - USTC claude-haiku-4-5 多模态调用不稳定，已加 60s timeout + 1 retry + safe fallback
   - 多数审片实际 auto-pass（超时降级），仅前 2 次成功返回详细反馈
4. **review_character 超时**：4 张 512×512 预览图 + 1 ref，payload 较大，通常超时 auto-pass
