# Vidance 使用指南

> 统一入口 `python core/vidance.py <子命令> [参数]`

## 三种子命令

| 子命令 | 输入 | 引擎 | 角色 | 后处理 | 适用场景 |
|--------|------|------|------|--------|----------|
| `auto` | 一句中文概念 | Wan T2V/I2V + FLUX | 文字描述→FLUX 生图 | 可选 | 快速出片、自动编剧 |
| `custom` | 参考图 + 预写脚本 | H3 ref2va | 三视图直接注入 | 可选 | 精确控制分镜、多角色 |
| `quick` | 一句中文概念 | Wan T2V | 无 | 无 | 最快出片、无角色 |

---

## auto — 概念自动生成

LLM 自动编剧 → 角色锚生成 → 逐镜生成 → 审片 → 后处理合成。

```bash
# 基本用法（无角色，纯 T2V + 配音 + 后处理）
python core/vidance.py auto "一只猫在月球上跳舞"

# 带角色（默认 auto 模式：先试 3DGS，审查不过降级 flux）
python core/vidance.py auto "深海探险" --character "蓝色水母"

# flux 模式（每镜 FLUX 直接生成角色+场景，质量最稳定）
python core/vidance.py auto "雪山日出" --character "红色小狐狸" --character-mode flux

# 全功能（RIFE 过渡 + LUT 调色 + BGM + STT 字幕）
python core/vidance.py auto "雪山日出：小狐狸的冒险" \
    --character "红色小狐狸" --character-mode flux \
    --lut cool --bgm calm --stt

# 禁用部分后处理
python core/vidance.py auto "概念" --character "角色" --no-rife --no-color --no-bgm
```

### auto 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `concept`（位置参数） | 视频概念（中文） | 必填 |
| `--character` | 角色描述（中文） | 无（纯 T2V） |
| `--character-mode` | `auto`/`3dgs`/`flux` | `auto` |
| `--voice` | TTS 音色（如 `edge-moe`） | config 默认 |

---

## custom — 参考图 + 脚本生成

用预写分镜脚本 + 角色三视图参考图，通过 H3 ref2va 一步生成带原生音频的视频。

```bash
# 基本用法（双角色故事）
python core/vidance.py custom \
    --ref input/doubao.jpg --ref input/naiwa.jpg \
    --script input/prompt1.txt

# 加后处理
python core/vidance.py custom \
    --ref input/doubao.jpg --ref input/naiwa.jpg \
    --script input/prompt1.txt \
    --lut cinematic --bgm dramatic --stt

# 高保真模式（参考图 2048px，角色身份保真更好，慢 2-3x）
python core/vidance.py custom \
    --ref input/doubao.jpg \
    --script input/prompt1.txt \
    --ref-image-size max

# 指定输出路径
python core/vidance.py custom \
    --ref input/doubao.jpg --ref input/naiwa.jpg \
    --script input/prompt1.txt \
    -o my_video.mp4
```

### custom 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--ref`（可重复） | 角色参考图路径 | 必填 |
| `--script` | 分镜脚本文件路径 | 必填 |
| `--ref-image-size` | `match`（快）/ `max`（2048px 高保真） | `match` |

### 分镜脚本格式

```
[全局场景设定行]
[全局角色设定行]
【01A｜标题｜约3秒】
生成提示词：
[镜头描述（可多行）]
【01B｜标题｜约2.5秒】
生成提示词：
[镜头描述]
【剪辑与声音...】（忽略）
```

- `约X秒` 支持小数（如 `约2.5秒`）
- 每镜 H3 最少 5 帧，不足自动对齐到 17k+5 网格
- `<Picture i>` 标签自动引用第 i 张参考图

---

## quick — 纯 T2V

无角色锚、无后处理，最快出片。

```bash
python core/vidance.py quick "一只猫在月球上跳舞"

# 指定音色
python core/vidance.py quick "概念" --voice edge-xiaoxiao
```

---

## 共享后处理参数（auto + custom）

| 参数 | 说明 |
|------|------|
| `--lut <style>` | 指定 LUT 风格：`cinematic`/`warm`/`cool`/`vintage`/`vivid`/`soft` |
| `--no-color` | 禁用 LUT 调色 |
| `--bgm <mood>` | 指定 BGM mood：`calm`/`uplifting`/`mysterious`/`dramatic`/`playful` |
| `--no-bgm` | 禁用背景音乐 |
| `--no-rife` | 禁用 RIFE 镜头间过渡 |
| `--stt` | 使用 faster-whisper STT 字幕对齐 |
| `-o/--output` | 输出路径（相对路径自动 resolve 到 output_dir） |

不传 `--lut`/`--bgm` 时，由 LLM 根据概念自动选择风格。`--no-rife`/`--no-color`/`--no-bgm` 覆盖 config 中的 `enabled: true`。

---

## 输出

- 不传 `-o`：自动输出到 `{output_dir}/{custom_}TIMESTAMP/final.mp4`
- 传 `-o` 相对路径：自动 resolve 到 `{output_dir}/` 下
- 传 `-o` 绝对路径：按指定路径输出
- 每次任务生成 `meta.json`（脚本/prompt/seed/时间戳等完整元数据）

---

## TTS 音色

| 前缀 | 引擎 | 示例 |
|------|------|------|
| `edge-*` | edge-tts（微软在线） | `edge-moe` 萌系高音 / `edge-xiaoxiao` 晓晓 / `edge-yunjian` 云健 |
| `cosy-*` | CosyVoice（本地 GPU） | `cosy-default` / `cosy-cross` |
| `cosy-<自定义>` | CosyVoice 克隆 | 在 `voices/<名>/prompt.wav` 放素材自动注册 |

> custom 模式用 H3 原生音频，不使用 TTS。`--voice` 仅对 auto/quick 有效。

---

## 服务依赖

运行前需确认对应 ComfyUI 实例已启动：

| 子命令 | 需要的服务 | 端口 |
|--------|-----------|------|
| auto | Wan (T2V/I2V) + FLUX/TripoSplat | 8189 + 8192 |
| auto（3dgs/auto 模式） | 额外需要 TripoSplat | 8192 |
| custom | H3 ref2va | 8188 |
| quick | Wan T2V | 8189 |
| 所有（带 `--voice`） | TTS 服务 | 9880 |
| 所有（带 `--stt`） | faster-whisper（GPU） | 内嵌 |

---

## 向后兼容

旧入口仍可用，但不推荐：

```bash
python core/pipeline.py ...      # 等同 auto
python core/custom_gen.py ...    # 等同 custom（不含后处理参数）
```

---

## 完整示例

### 示例 1：auto 全自动带角色

```bash
python core/vidance.py auto "雪原上的红斗篷少年" \
    --character "穿红色斗篷的短发少年" \
    --character-mode flux \
    --voice edge-moe \
    --lut cool --bgm calm \
    --stt
```

流程：LLM 编剧 → FLUX 生角色图 → 逐镜 FLUX 场景图 + Wan I2V + TTS → 审片 → RIFE 过渡 → LUT 调色 → BGM ducking → STT 字幕

### 示例 2：custom 双角色故事

```bash
python core/vidance.py custom \
    --ref input/doubao.jpg --ref input/naiwa.jpg \
    --script input/prompt1.txt \
    --ref-image-size match \
    --lut cinematic --bgm dramatic
```

流程：解析脚本 → LLM 看参考图描述角色 → H3 ref2va 逐镜生成（带原生音频）→ RIFE 过渡 → LUT 调色 → BGM ducking

### 示例 3：quick 极速出片

```bash
python core/vidance.py quick "一只猫在月球上跳舞"
```

流程：LLM 编剧 → Wan T2V → TTS 配音 → ffmpeg 合成（无调色无 BGM 无过渡）
