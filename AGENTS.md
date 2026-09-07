# Vidance — AI 有声短片生成

> 本文件供 opencode agent 读取，了解项目约定与命令。

## 项目概述

Vidance 是一个基于 opencode agent 编排的本地视频生成系统。核心流程：中文概念→LLM编剧→Wan T2V生成→CosyVoice配音→多模态审片→ffmpeg合成→有声短片。

## 目录结构

```
vidance/
├── opencode.json              # opencode 配置（agents + permissions）
├── AGENTS.md                  # 本文件
├── config/config.json         # 运行时配置（API key、引擎地址、模型名）
├── core/pipeline.py           # 端到端流水线主控
├── utils/
│   ├── comfy_api.py           # ComfyUI HTTP 客户端（Wan T2V）
│   ├── llm.py                 # USTC LLM 客户端（编剧/prompt优化/审片）
│   ├── tts.py                 # CosyVoice TTS 客户端
│   ├── tts_server.py          # CosyVoice FastAPI 服务（GPU2:9880）
│   ├── ffmpeg_tools.py        # 抽帧/SRT/合成
│   └── workflows/wan_t2v.json # Wan T2V workflow 模板
├── .opencode/
│   ├── agents/{director,reviewer}.md
│   └── skills/{scriptwriting,review}/SKILL.md
├── docs/                      # 设计文档
└── output/ → /mnt/dataset/... # 成片 + 元数据（软链到机械盘）
```

## 运行命令

### 全自动生成短片
```bash
python core/pipeline.py "一只猫在月球上跳舞"
```

### 启动 TTS 服务
```bash
conda activate cosyvoice
CUDA_VISIBLE_DEVICES=2 python utils/tts_server.py &
```

### 单步操作
```bash
# 编剧
python utils/llm.py --concept "概念"

# T2V 生成
python utils/comfy_api.py "english prompt" -o output/clips/shot_1.mp4

# TTS 配音
python utils/tts.py "中文旁白" -o output/clips/shot_1.wav

# 抽帧
python utils/ffmpeg_tools.py frames output/clips/shot_1.mp4 -n 4
```

## 服务端口

| 服务 | 端口 | GPU | 环境 |
|------|------|-----|------|
| Wan T2V (ComfyUI) | 8189 | GPU2 | comfyui |
| HunyuanVideo (ComfyUI) | 8190 | GPU3 | comfyui |
| SDXL (ComfyUI) | 8191 | GPU1 | comfyui |
| FLUX (ComfyUI) | 8192 | GPU0 | comfyui |
| CosyVoice TTS | 9880 | GPU2 | cosyvoice |

## 关键约定

1. **随机 seed**：每次 T2V 生成必须用随机 seed（ComfyUI 缓存命中问题）
2. **每镜 ≤5s**：Wan 单次上限 121 帧≈5s@24fps
3. **旁白字数**：duration × 4 字（中文约 4 字/秒）
4. **审片阈值**：综合分 ≥7 通过，最多重试 2 次，取最高分兜底
5. **时长对齐**：成片按配音时长为准，画面不足定格末帧
6. **元数据完整**：每次任务记录 meta.json（脚本/prompt/seed/审片/时间戳）

## Python 环境

- **主环境 (comfyui)**：`/home/zxy/.conda/envs/comfyui/bin/python`（torch 2.13+cu130）
  - 用于：pipeline、comfy_api、llm、ffmpeg_tools、moviepy
- **TTS 环境 (cosyvoice)**：`/home/zxy/.conda/envs/cosyvoice/bin/python`（torch 2.3.1+cu121）
  - 用于：tts_server（CosyVoice 推理）

## LLM 模型

| 模型 | 用途 |
|------|------|
| deepseek-v4-flash | 编剧、prompt 优化 |
| claude-sonnet-4-6 | 多模态审片 |
| claude-haiku-4-5 | 快速审片（备选） |
