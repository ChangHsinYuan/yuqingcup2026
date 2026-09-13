# Vidance v0 使用指南 — 有声短片 MVP

> v0 是视频生成系统的第一个可用版本："概念 → 有声短片"端到端。纯 T2V（文生视频，无角色/无 I2V/无 3D），配套双引擎 TTS 配音 + 多模态审片 + ffmpeg 合成。
>
> 主流程见 [usage.md](./usage.md)，其他版本：[usage-v1.md](./usage-v1.md)（角色锚/3DGS）/[usage-v2.md](./usage-v2.md)（RIFE/LUT/BGM/ffmpeg/STT）/[usage-v3.md](./usage-v3.md)（mesh/资产库）/[usage-v4.md](./usage-v4.md)（爬虫/队列/声音克隆）

## v0 是什么

v0 验证了"概念 → 有声短片"的完整链路，是后续所有版本（v1 角色一致性 / v2 长视频 / v3 3D / v4 流水线）的地基。

```
概念 → LLM 编剧 → 分镜脚本(含旁白) → 每镜英文 prompt
  → Wan T2V 逐镜生成 → TTS 双引擎配音 → 多模态 LLM 审片 → ffmpeg 合成(拼接+配音+字幕+转场)
  → output/{task_id}/final.mp4 + meta.json
```

**特征**：纯 T2V（无参考图）、单镜头 ≤5s（Wan 121 帧上限）、2-5 镜、10-30s 成片、审片不过自动重做（≤2 次）。

## 一键出片

v0 的链路现在统一收敛到 `vidance.py` 的 **`quick`**（最快、无角色无后处理）和 **`auto`**（默认也走 T2V，无 `--character` 时即 v0 流程）两个子命令。

```bash
# quick：一句话最快出片（LLM 编剧 → Wan T2V → TTS 配音 → ffmpeg 合成）
python core/vidance.py quick "一只猫在月球上跳舞"

# auto 不带角色 = v0 纯 T2V 流程（走 LLM 编剧 + 审片 + 可选后处理）
python core/vidance.py auto "一只猫在月球上跳舞"

# 指定音色与输出
python core/vidance.py quick "深海探险" --voice edge-xiaoxiao -o output/my_video.mp4
```

## 底层组件（v0 使用的工具）

v0 没有独立专属工具，全部复用现有 utils，可单独调用：

| 组件 | 模块 | 用途 |
|------|------|------|
| 编剧 | `utils/llm.py` | 中文概念 → 分镜脚本（含旁白） |
| 文生视频 | `utils/comfy_api.py` | Wan T2V 逐镜生成 |
| 配音 | `utils/tts.py` + `utils/tts_server.py` | 旁白 → 音频（edge/CosyVoice 双引擎） |
| 审片 | `utils/llm.py`（`review_shot`） | 抽帧多模态评分，不过重做 |
| 合成 | `utils/ffmpeg_tools.py` | 拼接 + 配音 + 烧字幕 + 转场 |

### 单步命令

```bash
# 编剧（查看 LLM 生成的脚本）
python utils/llm.py --concept "一只猫在月球上跳舞"

# 单镜 T2V（Wan，需 8189）
python utils/comfy_api.py "a cat dancing on the moon, cinematic" -o output/clips/shot_1.mp4

# 单镜配音（需 TTS 服务 9880 在线）
python utils/tts.py "一只猫在月球上跳舞" -v edge-moe -o output/clips/shot_1.wav

# 列出所有可用音色
python utils/tts.py --list-voices

# 合成成片（拼接 + 配音 + 字幕）
python utils/ffmpeg_tools.py compose \
    output/clips/shot_1.mp4 ... \
    --audio output/clips/shot_1.wav ...  # 具体参数见 usage-v2.md
```

### 服务依赖

| 操作 | 需要的服务 | 端口 |
|------|-----------|------|
| LLM 编剧/审片 | USTC 外部 API | — |
| Wan T2V | ComfyUI Wan (GPU2) | 8189 |
| TTS 配音 | TTS 双引擎 (GPU2) | 9880 |
| 合成 | ffmpeg（本机） | — |

## 元数据

每次任务在 `{output_dir}/output/{task_id}/` 下产生：

```
final.mp4        # 成片
meta.json        # 脚本/prompt/seed/审片结果/时间戳/音色
clips/           # 各镜视频 + 配音
```
