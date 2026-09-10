# Vidance — AI 有声短片生成

> 本文件供 opencode agent 读取，了解项目约定与命令。

## 项目概述

Vidance 是一个基于 opencode agent 编排的本地视频生成系统。核心流程：中文概念→LLM编剧→Wan T2V生成→双引擎TTS配音→多模态审片→ffmpeg合成→有声短片。

**当前状态：v2 长视频完成**（2026-09-09），详见 [docs/v2-design.md](./docs/v2-design.md)。

v1 角色锚流程：FLUX 生角色图 → TripoSplat 重建 3DGS → 每镜 RenderSplat 按角度渲染参考帧 → Wan I2V 生成。

**v2 长视频**（M1-M5 全部完成）：
- **M1 RIFE 光流插帧**：镜头间光流过渡（rife_v4.26，multiplier=8，0.375s@24fps）
- **M2 镜头并行预取**：ThreadPool 并行预取 FLUX 参考帧 + TTS 配音（与 Wan I2V 串行不冲突）
- **M3 ffmpeg 调色**：6 种 3D LUT（cinematic/warm/cool/vintage/vivid/soft），纯 numpy 生成，LLM 自动选风格
- **M4 配乐 ducking**：5 种 BGM（calm/uplifting/mysterious/dramatic/playful），纯 numpy 合成，sidechaincompress ducking
- **M5 faster-whisper STT**：large-v3-turbo 模型，成片音频转写→SRT→重新烧录字幕

**v1.1 改进**：
- **I2V 修复**：`WanImageToVideo` → `Wan22ImageToVideoLatent`（Wan 2.2 原生 48ch latent + noise_mask inpainting），首帧与参考图相关性 0.99+
- **character-mode 开关**：`auto`（默认，3DGS 审查不过自动降级 flux）/ `3dgs` / `flux`（每镜 FLUX 直接生成角色+场景图，不重建 3D）
- **3DGS 位姿修复**：PCA 自动对齐角色主轴到垂直 + 裁剪接地合成（脚踩地不悬浮）
- **3DGS 渲染优化**：飞点过滤 + 超采样渲染 + 大高斯参数，表面更平滑噪声更少
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
│   ├── ffmpeg_tools.py        # 抽帧/SRT/合成（含 LUT 调色 + BGM ducking）
│   ├── rife.py                # v2 RIFE 插帧客户端（镜头间过渡 + slowmo）
│   ├── gen_luts.py            # v2 生成 3D LUT .cube 文件（6 种风格）
│   ├── music.py               # v2 生成环境配乐 BGM（5 种风格，numpy 合成）
│   ├── stt.py                 # v2 faster-whisper STT 转写（字幕兜底）
│   ├── luts/                  # v2 LUT 文件目录（cinematic/warm/cool/vintage/vivid/soft .cube）
│   └── workflows/
│       ├── wan_t2v.json       # Wan T2V workflow 模板
│       ├── wan_i2v.json       # Wan I2V workflow 模板（12 节点，Wan22ImageToVideoLatent）
│       ├── triposplat.json    # TripoSplat 单图→PLY workflow（13 节点）
│       ├── flux_t2i.json      # FLUX T2I workflow 模板（10 节点）
│       └── rife_transition.json  # v2 RIFE 插帧 workflow（7 节点）
├── voices/                    # 自定义 CosyVoice 克隆音色素材目录
├── bgm/                       # v2 自定义 BGM 素材目录（放 {mood}.wav 自动使用）
├── .opencode/
│   ├── agents/{director,reviewer}.md
│   └── skills/{scriptwriting,review}/SKILL.md
├── docs/                      # 设计文档（roadmap + v0-design + v1-design）
├── voice_samples/ → /mnt/dataset/...  # 音色试听样本（软链）
└── output/ → /mnt/dataset/... # 成片 + 元数据（软链到机械盘）
```

## 运行命令

### 全自动生成短片（v2 长视频 + v1.1 角色一致性）
```bash
# v2 全功能：RIFE 过渡 + 并行预取 + LUT 调色 + BGM ducking + STT 字幕
python core/pipeline.py "雪山日出：小狐狸的第一次冒险" --character "红色小狐狸" --character-mode flux --stt

# v1.1 auto 模式（默认）：先试 3DGS，审查不过自动降级 flux
python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫"

# flux 模式：每镜 FLUX 直接生成角色+场景图，不重建 3D（推荐，质量更稳定）
python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫" --character-mode flux

# 指定调色风格和配乐 mood
python core/pipeline.py "深海探险" --character "蓝色水母" --character-mode flux --lut cool --bgm mysterious

# 禁用部分功能
python core/pipeline.py "概念" --character "角色" --no-rife --no-color --no-bgm
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

试听样本：`vidance/voice_samples/`（17 个 wav）。默认音色 `edge-moe`（config.json，萌系高音-哈基米风）。

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
   - `3dgs`：FLUX 图 → TripoSplat→3DGS → 每镜 RenderSplat 按角度渲染 → composite bg → Wan I2V（所有镜头强制 3DGS）
   - `auto`（默认）：先走 3DGS 流程 + review_character 审查，不过则自动降级 flux
8. **审片 5 维**（v1）：consistency/quality/motion/artifact/character_consistency，与角色参考图对比
9. **审片图片缩放**：上传前 resize 到 768px + JPEG 85%（原始 832×480 太大导致 API 超时）
10. **审片超时处理**：60s timeout + 1 retry，失败则 safe fallback auto-pass（不阻塞流水线）
11. **RIFE 过渡**（v2）：镜头间提取首尾帧 → RIFE 光流插帧（multiplier=8）→ 0.375s 过渡片段，config `rife.enabled`
12. **镜头并行预取**（v2）：ThreadPool 并行预取 FLUX 参考帧 + TTS 配音，与 Wan I2V 串行不冲突
13. **LUT 调色**（v2）：6 种 3D LUT 程序生成（`gen_luts.py`），LLM 自动选风格，ffmpeg `lut3d` 滤镜，config `color.enabled`
14. **BGM ducking**（v2）：5 种 BGM 程序合成（`music.py`），ffmpeg `sidechaincompress` 配音时自动降 BGM，config `bgm.enabled`
15. **STT 字幕兜底**（v2）：faster-whisper large-v3-turbo 转写成片音频→SRT→重新烧录，`--stt` 开关，默认关闭（TTS 时间戳通常够用）

## Python 环境

- **主环境 (comfyui)**：`/home/zxy/.conda/envs/comfyui/bin/python`（torch 2.13+cu130）
  - 用于：pipeline、comfy_api、llm、ffmpeg_tools、moviepy、rife、stt、music、gen_luts
- **TTS 环境 (cosyvoice)**：`/home/zxy/.conda/envs/cosyvoice/bin/python`（torch 2.3.1+cu121）
  - 用于：tts_server（CosyVoice 推理）
- **STT 模型**：`/mnt/dataset/zxy/hf_cache/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/`（int8_float16 GPU，HF_HOME 指向 hf_cache）

## LLM 模型

| 模型 | 用途 |
|------|------|
| deepseek-v4-flash | 编剧、prompt 优化、LUT 风格选择、BGM mood 选择 |
| claude-haiku-4-5 | 多模态审片（主力，4s/镜） |
| claude-sonnet-4-6 | 审片备选（review_strict，534s/镜太慢） |

## 已知限制（v2）

1. **3DGS 角色重建质量仍是瓶颈**（character_consistency 2-4/10）：
   - TripoSplat 262K 高斯渲染稀疏，像素覆盖 31-35%（v1.1 位姿修复后提升，原 13-26%）
   - 3DGS 颜色偏暗（mean 0.51 vs FLUX 原图 0.94），宇航服细节丢失
   - **v1.1 位姿修复**：`load_ply` PCA 自动对齐角色主轴到垂直（替代手动 Y/Z 交换），`composite_bg` 裁剪角色 bbox → 缩放 55% 画面高 → 接地放置 88% 位置（脚踩地不悬浮）
   - **v1.1 I2V 修复**：Wan22ImageToVideoLatent（48ch + noise_mask），首帧与参考图相关性 0.99+
   - **v1.1 缓解**：`flux` 模式跳过 3D 重建；`auto` 模式 3DGS 审查不过自动降级 flux
   - **v1.1 渲染优化**：`_filter_floaters` 过滤孤立高斯（距离>99th pct + opacity<0.03），渲染参数 min_px 3→5、gain 2→3、supersample=2（2x 渲染→LANCZOS 缩放），LLM 确认"噪声显著减少，表面更平滑"
   - **剩余问题**：3DGS 镜头类型受限（只能全身远景/中景，无法特写），部分角度重建质量不均
   - **后续**：v2 探索多图 3D 重建 / 参考图 ControlNet / IP-Adapter 角色锁定 / 表面平滑
2. **flux 模式角色一致性依赖 FLUX prompt**：侧面/背面镜头（yaw≠0）FLUX 生成角色可能走样，无 3D 约束
3. **多模态审片 API 延迟波动大**（12s-200s+）：
   - USTC claude-haiku-4-5 多模态调用不稳定，已加 60s timeout + 1 retry + safe fallback
   - 多数审片实际 auto-pass（超时降级），仅前 2 次成功返回详细反馈
4. **review_character 超时**：4 张 512×512 预览图 + 1 ref，payload 较大，通常超时 auto-pass
5. **BGM 为程序合成**（v2）：numpy 生成简单环境音乐，质量有限，留 `bgm/` 自定义目录供放入真实素材
6. **LUT 为程序生成**（v2）：numpy 生成 6 种风格 3D LUT，效果不如专业 LUT 包，留 `color.lut_dir` 自定义路径
7. **STT 默认关闭**（v2）：TTS 时间戳通常够用，`--stt` 仅在需要重新对齐时开启（额外 GPU 显存+耗时）
8. **视频编码统一 yuv420p**（v2 修复）：LUT 调色 + STT 烧录的 ffmpeg 命令均加 `-pix_fmt yuv420p`，确保所有播放器兼容（之前 lut3d 滤镜导致输出 yuv444p，部分播放器无法播放）
9. **长片未验证**（v2 backlog）：LLM 编剧 prompt 硬编码 "2-5个镜头"（`llm.py:116`），3 次测试均为 3-4 镜/10-20s，未跑过 5-15 镜长片
10. **单镜慢动作未接入**（v2 backlog）：`rife.py:91` `slowmo()` 方法已实现，但 pipeline 从未调用，LLM prompt 也不生成 `shot.slowmo` 字段
11. **per-shot 过渡类型未实现**（v2 backlog）：pipeline 对所有相邻镜头统一 RIFE，不读 `transition_out` 字段，大跨场景无法降级 crossfade
12. **后期审片 4 维未实现**（v2 backlog）：设计 §8.1 定义 color_consistency/bgm_fit/transition_smooth/audio_balance，pipeline 无整片审片环节（逐镜审片复用 v1 四维）
13. **music subagent 简化**（v2 偏差）：设计 §8.2 定义独立 music subagent，实际简化为 pipeline 内联 LLM 调用（`llm.select_bgm_mood()`），功能等价
14. **无 `--duration` CLI**（v2 backlog）：设计 §10 验收命令含 `--duration 90`，实际未加 argparse 参数
