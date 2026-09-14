# Vidance v8 设计文档 — 一镜到底

> 用户编号：**v9**。仓库版本号 v8（紧接 v7 之后）。
> 状态：🎯 待开发。目标：突破单镜 ≤5s 限制，实现 30s+ 连续性镜头。

---

## 1. 概述

### 1.1 核心限制

- Wan 单次生成上限 **121 帧 ≈ 5s @24fps**（AGENTS 约定 2）
- 现有长片靠"分镜拼接"，镜头之间有跳变，无法真·一镜到底

### 1.2 v8 目标

- **链式 I2V**：段 N 末帧 → 段 N+1 首帧条件，拼缝隐形（画面连续）
- **LLM 连续运镜脚本**：一段式剧本拆成首尾连续的子段，保持镜头连续性
- **漂移控制**：每段重注入角色锚（v1 3DGS/mesh 或 FLUX 参考帧），防跨段物像漂移
- `--oneshot --duration N` 一键生成长连续镜头（目标 30s+）
- **不做真·无限长**（显存/上下文限制，roadmap 明示）

### 1.3 v8 范围

- `utils/oneshot.py`（链式 I2V 编排）+ `--oneshot` 接入 auto
- `llm.oneshot_script()`：一段场景 → 连续运镜的子段描述（每段起止机位/动作连续）
- 末帧回灌（reuse Wan `Wan22ImageToVideoLatent` noise_mask 尾帧条件）
- 跨段锚注入（角色参考帧每段喂入，参考 v1/v3 锚流程）
- 可接 v5 特效/punch-in/slowmo 增强连续性

### 1.4 核心验证点

- 2 段拼缝肉眼无跳变（末帧→首帧相似度高）
- 30s 一镜到底端到端出片，角色不漂移
- 运镜脚本 LLM 生成可执行，各子段能接续

---

## 2. 架构设计

### 2.1 链式 I2V 原理

```
scene → LLM 拆连续子段 [s1..sn]（每段含目标运镜/动作/机位）
s1: I2V(ref帧, prompt) → 视频1，取末帧 F1
s2: I2V(ref帧=F1 尾帧, 首帧条件 + 角色锚) → 视频2，取末帧 F2
... sn
→ 拼接（可 crossfade 很短 0.1s 或直接硬切，拼缝因条件一致而隐形）
→ RIFE slowmo/插帧平滑
```

关键技术：**段 N+1 的首帧直接用段 N 的末帧**，画面连续；角色锚防漂移。

### 2.2 运镜脚本（llm 扩展）

```python
def oneshot_script(concept: str, duration: int) -> list[dict]:
    # 输出各子段：{shot_desc, motion, start_pose, end_pose, prompt}
    # 强约束：相邻段机位/动作连续（不想接续就标 cut 给用户）
    # chat_json
```

默认拆分：每段 ~4-5s（受 Wan 帧上限），duration/4.5 段数。

### 2.3 漂移控制（锚注入）

- 每段 I2V 的 ref 帧 = 前段末帧（保证连续）
- 另喂一个**角色锚帧**（FLUX 角色图 / 3DGS/mesh 按机位渲染，参考 v1/v3）+ prompt 强调角色，抑制脸/形体漂移
- 段间用 `_build_character_anchor` 复用 v3 asset_registry 资产库

### 2.4 混合模式

- 子段拆分 + 回灌硬编码，运镜创意 LLM 生成（roadmap §7.1）

---

## 3. 核心流程

```
[1] concept/脚本 → oneshot_script() → 连续子段链
[2] 循环：I2V(本段条件=上段末帧 + 角色锚) → 取末帧
[3] 拼接（拼缝隐形）+ RIFE 平滑
[4] 后处理（v5 特效可选 / LUT / BGM / STT）与配音时长对齐
[5] 出片 meta.json 记录子段链 + 末帧回灌
```

单段仍受 ~5s 上限，靠链式拼出长连续；总时长由段数决定（显存/时间随段数线性增）。

---

## 4. 接口规格

### 4.1 CLI

```
python core/vidance.py auto "穿越隧道的光" --oneshot --duration 30 [--character X] [--anchor mesh|3dgs|flux]
python core/vidance.py auto "..." --oneshot 30          # 无角色纯场景一镜
# 默认（无 --oneshot）行为不变（分镜拼接）
```

### 4.2 任务选项

`options.oneshot = true`、`options.duration`（目标秒）、`options.anchor`。

---

## 5. GPU 与资源

- 每段一次 Wan I2V（GPU2:8189），n 段 = n 次，串行（末帧依赖）
- 时长线性增长：30s ≈ 7 段 ≈ 7×单段耗时（几分钟到十几分钟）
- 角色锚 mesh/3dgs 用 GPU1:8193 / FLUX GPU2，按 v1/v3 复用
- 显存：单段内模型常驻，段间复用上下文，不随段数爆显存（每段独立 I2V）

---

## 6. 验收标准

1. 2+ 段拼缝肉眼无跳变（末帧→首帧相似度 ≥0.9 可量化抽查）
2. `--oneshot --duration 30` 端到端出片，角色不漂移
3. oneshot_script() 子段链相邻连续可执行
4. 无角色纯场景一镜也成立
5. meta.json 记录子段链/回灌/锚，可审计

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 段间跳变 | 首帧=上段末帧 + 短 crossfade（0.1s）兜底 |
| 角色漂移 | 每段重注入角色锚帧 + prompt 强调 |
| 运镜脚本不连续 | LLM 强约束相邻连续 + 悬吊，不可续就标 cut 显式告知 |
| 全段失败返工 | 单段可独立重试，复用 v2 逐镜重试上限 2 次 |
| LLM 拆分不稳 | chat_json 兜底等长拆分 |

---

## 8. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M1 | oneshot.py 链式 I2V（末帧回灌 + 拼接） | 链式核心 |
| M2 | oneshot_script() LLM 连续运镜脚本 | 运镜拆段 |
| M3 | 漂移控制（角色锚注入）+ 段重试 | 稳定性 |
| M4 | `--oneshot` 接入 auto + 30s 端到端 | v8 完成 |

---

## 9. 依赖与前置

- 复用：utils/comfy_api.py（I2V）、utils/rife.py（插帧）、utils/asset_registry.py + utils/mesh_render.py + splat_renderer.py（角色锚）、v5 effects（可选增强连续性）、v2 后处理链
- 复用 v4 M6 fastline 的选图/伪视频可作 `--oneshot-street`（暂不入）
- 无新外部服务；依赖 Wan I2V（已部署 GPU2:8189）
