# Vidance v5 设计文档 — 剪辑特效包

> 用户编号：**v6**。仓库版本号 v5（紧接 v4 营销号流水线之后）。
> 状态：🎯 待开发。前置：v4 M1-M5 完成 + M6 快速链路（复用选图/后处理）。

---

## 1. 概述

### 1.1 v4 遗留问题

- 镜头间只有 RIFE 过渡 / crossfade / cut，镜头内无表现力
- 营销号"抓眼球"靠节奏，缺闪回/甩镜/变速/punch-in 等剪辑语言
- 特效全部手写 ffmpeg 命令，不可复用、不可由 LLM 自动选

### 1.2 v5 目标

- 建立**可复用的特效库**（`utils/effects.py`），每种特效一个函数
- LLM 按剧本情绪 / 分镜内容**自动选**每镜特效（`llm.select_effects()`）
- `--effects auto` 一键接入现有 auto / fast 链路，`--effects off` 回退原行为
- concat / 镜头间转场支持 xfade 全 30 种（v2.1 只有 cut/rife/crossfade 三种）

### 1.3 v5 范围

- 镜头内特效：闪白 / 闪黑（闪回）、zoom punch-in、glitch、胶片颗粒 + vignette、speed ramp 变速、定格放大、甩镜（wipes/pan）
- 镜头间转场：复用 xfade 内置 30+ 种（slide/wipe/radial/pixelize/hblur/fade...），扩充 concat
- `select_effects_mood()` LLM 判断：输入剧本（原文 + 分镜情绪标注）→ 每镜特效 + 镜头间转场
- AGENTS 约定：接入 pipeline 后处理（先 RIFE/LUT/BGM/STT，特效在其间或最后，需定义顺序）

### 1.4 核心验证点

- 单镜特效各能独立产出正确视频（时长/尺寸/yuv420p 收敛）
- xfade 转场 ≥5 种在 concat 中正确，且与配音时长对齐
- `auto --effects auto` 端到端出片，特效不破坏配音/字幕/BGM
- 特效失败不影响出片（safe fallback → 原图直出）

---

## 2. 架构设计

### 2.1 特效库接口（utils/effects.py）

```python
class Effects:
    # 镜头内特效：输入单镜视频（或图片段）→ 输出加特效段
    def flash(self, clip, kind="white", t=0.1): ...        # 闪白/闪黑（闪回起点）
    def punch_in(self, clip, strength=1.2, cx=0.5, cy=0.5): # 快速推进强调
    def glitch(self, clip, seed=None): ...                  # 数字故障脉冲
    def grain_vignette(self, clip, grain=0.02, vig=0.35): ...# 颗粒+暗角（胶片感）
    def speed_ramp(self, clip, factor=1.5): ...             # 变速（加快/慢）
    def freeze_zoom(self, clip, hold_t=0.4, strength=1.3):  # 定格放大
    def wipe(self, clip, direction="left"): ...             # 甩镜（平移扫过）

    # 镜头间：两 clip 用 xfade 转场拼接
    def transition(self, a, b, kind="crossfade", dur=0.4): ...
```

实现全部为 **ffmpeg filter 封装**（subprocess），输出统一 `yuv420p` + 与输入相同分辨率/时长基准。

### 2.2 LLM 自动选特效

```python
def select_effects(script: list[dict]) -> list[dict]:
    # 输入：分镜脚本（每镜原文 + 情绪/动作标注）
    # 输出：每镜 {effect: str, transition: str, params: {...}}
    # chat_json，无 llm 时全 "none"/"crossfade"（退化行为不影响出片）
```

判断要点：闪回→flash；强调/爆点→punch_in 或 freeze_zoom；紧张→glitch+快变速；情绪→grain_vignette；平淡叙事→none。

### 2.3 接入顺序（pipeline / fastline 后处理）

```
镜头生成/运镜
  → per-shot 特效（effects）
  → RIFE 过渡 / xfade 转场（镜头间）
  → LUT 调色（v2 6 风格）
  → BGM ducking（v2）
  → STT 字幕（v2，--stt）
  → 合成 yuv420p
```

特效放 LUT 前（特效是"画面语言"，调色是"整体风格"），order 可配置。

### 2.4 混合模式

- 确定性特效函数硬编码（ffmpeg 封装），创意/参数选择交 LLM——沿用 v4 混合模式原则（roadmap §7.1）

---

## 3. 核心流程

```
[1] 输入：分镜量脚本 script[]（原文+情绪）或 fastline 图片段
[2] select_effects() → per-shot 特效 + 镜头间转场清单
[3] 对每镜执行特效函数（ffmpeg filter）
[4] 镜头间按转场拼接（xfade 30 种 / rife / cut）
[5] LUT → BGM ducking → STT 字幕 → 合成
[6] 出片 + meta.json 记录 {effects, transitions}
```

---

## 4. 接口规格

### 4.1 CLI（vidance.py 扩展）

```
python core/vidance.py auto "概念" --character "角色" --effects auto
python core/vidance.py fast "文案" --images dir/ --effects auto
python core/vidance.py auto "概念" --effects off          # 回退原行为
python core/effects.py list        # 列出可用特效/转场
python core/effects.py apply in.mp4 --effect punch_in --params '{"strength":1.2}'
```

### 4.2 配置（config/config.json）

```json
"effects": { "enabled": true, "default_transition": "crossfade", "xfade_kind": "slide" }
```

### 4.3 任务选项（scheduler/API）

`options.effects = "auto" | "off"`（任务级覆盖 config）。

---

## 5. GPU 与资源

- 特效全部 **CPU ffmpeg**，几乎零 GPU 负担，可与视频生成/后处理并跑
- speed_ramp 用 `setpts`，不额外拉模型
- 与 v4 M6 快速链路天然契合（静态图 + 特效即可出伪视频，更省）

---

## 6. 验收标准

1. 8 种镜头内特效各能独立产出正确视频（yuv420p、时长≈预期）
2. xfade ≥5 种在 concat 正确，与配音时长对齐
3. `auto --effects auto` 端到端出片，特效不破坏配音/字幕/BGM
4. 特效函数抛错 → safe fallback 原直出，不阻塞流水线
5. meta.json 记录每镜生效的特效/转场（可审计）

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 特效不收敛（分辨率/时长漂移） | 统一封装 + 校验 yuv420p/尺寸/时长 |
| 特效破坏配音同步 | 特效仅画面，配音时间轴不变；验证对齐 |
| xfade 转场过多降低真实感 | LLM 保守选，默认 crossfade/slide |
| LLM 选特效不稳定 | chat_json 兜底 none；参数默认值稳健 |

---

## 8. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | effects.py 8 种镜头内特效 + 独立 CLI | 特效库 |
| M2 | xfade 转场封装 + concat 扩充 | 转场 |
| M3 | select_effects() LLM 自动选 + pipeline 接入 | 自动选 |
| M4 | 端到端验证（auto/fast 各一） | v5 完成 |

---

## 9. 依赖与前置

- 复用：utils/ffmpeg_tools.py（合成）、utils/rife.py（转场）、utils/llm.py（select_effects）
- 复用：v4 M6 fastline（图片段特效，验证伪视频增强）
- 无新外部服务；纯 ffmpeg + 现有 LLM
