# Vidance v2 使用指南 — 长视频小工具

> v2 长视频相关的小工具：RIFE 插帧过渡、LUT 调色、BGM 生成、ffmpeg 工具、STT 字幕对齐。
>
> 主流程见 [usage.md](./usage.md)，其他版本：[usage-v1.md](./usage-v1.md) / [usage-v3.md](./usage-v3.md) / [usage-v4.md](./usage-v4.md)

## 总览

| 工具 | 一句话 | 服务依赖 |
|------|--------|---------|
| `utils/rife.py` | 两帧光流插帧 → 过渡/慢动作视频 | Wan ComfyUI :8189 |
| `utils/gen_luts.py` | 6 风格 LUT cube 生成 | 无 |
| `utils/music.py` | 5 mood 合成 BGM | 无 |
| `utils/ffmpeg_tools.py` | 抽帧 / SRT 生成 / 最终合成 | 无（ffmpeg） |
| `utils/stt.py` | faster-whisper STT 字幕重对齐 | 无（GPU） |

---

## RIFE 插帧（utils/rife.py）

两帧之间光流插帧生成平滑过渡视频，v2 镜头衔接的核心。

### CLI

```bash
# 两帧插过渡（2帧间生成 multiplier-1 个中间帧，默认 8 → 7 中间帧）
python utils/rife.py frame_a.png frame_b.png -o output/trans_1.mp4 \
    -m 8 \        # --multiplier 插帧倍率
    --fps 24 \    # 输出帧率
    --model rife_v4.26.safetensors
```

### Python API

```python
from utils.rife import RIFEClient

rife = RIFEClient()   # 继承 ComfyClient，连 :8189（GPU2）
```

| 方法 | 说明 |
|------|------|
| `interpolate_transition(frame_a_path, frame_b_path, output_path, multiplier=8, fps=24, filename_prefix='vidance/transition')` | 两帧光流插帧 → 过渡视频片段 |
| `slowmo(video_path, output_path, multiplier=2, fps=24) -> dict` | 整段视频慢动作（抽首尾帧再插帧） |

> **注意**：
> - RIFE 随 Wan 共实例 8189，max_concurrent=1（并发安全）
> - `frames:2` 是审查抽帧用的参数，不是视频帧数

---

## LUT 调色（utils/gen_luts.py）

生成 6 种电影风格 .cube LUT（33³），供 compose 烧录调色用。

### CLI

```bash
python utils/gen_luts.py --size 33 --outdir config/luts
# 产出：cinematic / warm / cool / vintage / vivid / soft
```

### Python API

```python
from utils.gen_luts import generate_cube

generate_cube('cinematic', size=33, filepath='config/luts/cinematic.cube')
```

| 风格 | 效果 |
|------|------|
| cinematic | 电影感（青橙分离、压高光） |
| warm / cool | 暖调 / 冷调 |
| vintage | 复古褪色 |
| vivid | 高饱和 |
| soft | 柔光低对比 |

> LLM 自动选 LUT：`LLMClient.select_lut(concept, script, available_styles)`（v2 审查链路）

---

## BGM 生成（utils/music.py）

纯 numpy 合成 5 种 mood 的 BGM（ADSR 包络 + 和弦铺底 + 琶音），无需模型。

### CLI

```bash
python utils/music.py --mood calm --duration 30 -o output/bgm.wav
# -o 必填；mood ∈ calm / uplifting / mysterious / dramatic / playful
```

### Python API

```python
from utils.music import generate_bgm

generate_bgm(mood='calm', duration=30.0, output_path='output/bgm.wav')
```

| mood | 场景 |
|------|------|
| calm | 舒缓（纪录片/日常） |
| uplifting | 昂扬（励志/新闻） |
| mysterious | 悬疑 |
| dramatic | 戏剧冲突 |
| playful | 活泼（营销/萌宠） |

> LLM 自动选 mood：`LLMClient.select_bgm_mood(concept, script, available_moods)`

---

## ffmpeg 工具（utils/ffmpeg_tools.py）

### Python API

```python
from utils.ffmpeg_tools import get_duration, extract_frames, generate_srt, compose
```

| 函数 | 说明 |
|------|------|
| `get_duration(video_path) -> float` | 视频时长（秒） |
| `extract_frames(video_path, n_frames=4, output_dir=None, prefix=None) -> list` | 均匀抽 n 帧返回路径列表（审查用；默认输出到视频旁 `frames/`） |
| `generate_srt(timestamps, output_path, offset=0.0) -> str` | 时间戳列表 → SRT 字幕文件 |
| `compose(clips, audio_paths, srt_path=None, output_path='output/final.mp4', transition='crossfade', transition_duration=0.3, transition_clips=None, transition_types=None, lut_path=None, bgm_path=None, bgm_volume=0.3) -> str` | **最终合成**：拼接画面 + 合并配音 + LUT 调色 + 烧录字幕 + BGM ducking |

### compose 参数说明

| 参数 | 说明 |
|------|------|
| `clips` | 视频片段路径列表 `[shot_1.mp4, ...]` |
| `audio_paths` | 对应配音路径列表 `[shot_1.wav, ...]` |
| `transition_clips` | 镜头间 RIFE 过渡视频列表，长度 = len(clips)-1，None 则不用 |
| `transition_types` | 每个过渡的类型列表（覆盖全局 `transition`） |
| `lut_path` | .cube LUT 路径（None 不调色） |
| `bgm_path` + `bgm_volume` | BGM 路径 + 音量（默认 0.3，自动 ducking） |

> 输出统一 `yuv420p` + High profile（兼容性硬要求）

### CLI

```bash
# 抽帧
python utils/ffmpeg_tools.py frames video.mp4 -n 4 -o ./frames

# 生成 SRT
python utils/ffmpeg_tools.py srt timestamps.json -o output/subs.srt
```

---

## STT 字幕对齐（utils/stt.py）

faster-whisper large-v3-turbo（int8_float16），TTS 时间戳不精确时用 STT 重新对齐字幕。

### CLI

```bash
python utils/stt.py output/shot_1.wav -o output/shot_1.srt -l zh
# --word-timestamps 词级时间戳
```

### Python API

```python
from utils.stt import transcribe_audio, transcribe_to_srt

# 转写：返回 [{'text': '...', 'start': 0.0, 'end': 1.5}, ...]
result = transcribe_audio('shot_1.wav', language='zh')

# 直出 SRT
transcribe_to_srt('shot_1.wav', 'shot_1.srt', language='zh')
```

> 模型路径从 config 解析，实例缓存（`_MODEL_CACHE`）避免重复加载；language 默认 zh
