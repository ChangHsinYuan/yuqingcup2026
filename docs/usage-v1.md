# Vidance v1 使用指南 — 角色锚单步工具

> v1 角色一致性相关的单步小工具：ComfyUI 客户端（T2V/I2V/TripoSplat/FLUX）+ 3DGS 多角度渲染器。
>
> 主流程见 [usage.md](./usage.md)，其他版本：[usage-v2.md](./usage-v2.md) / [usage-v3.md](./usage-v3.md) / [usage-v4.md](./usage-v4.md)

## 总览

| 工具 | 一句话 | 服务依赖 |
|------|--------|---------|
| `utils/comfy_api.py` | ComfyUI HTTP 客户端（T2V/I2V/3DGS/FLUX/H3） | 按方法不同 |
| `utils/splat_renderer.py` | 3DGS PLY 多角度渲染 + 角色抠像合成背景 | 无（纯 CPU） |

---

## ComfyUI 客户端（utils/comfy_api.py）

### CLI — Wan T2V 直出

```bash
# 基本用法（英文 prompt 效果最好）
python utils/comfy_api.py "a cat dancing on the moon" -o output/clips/shot_1.mp4

# 全参数
python utils/comfy_api.py "a cat dancing on the moon" \
    -o output/clips/shot_1.mp4 \
    --seed 42 \        # 不传则随机（推荐，防 ComfyUI 缓存命中）
    --width 1280 --height 704 \
    --length 121 \     # 帧数，121帧≈5s@24fps（Wan 单次上限）
    --steps 20 --cfg 5.0
```

### Python API — 全部生成端点

```python
from utils.comfy_api import ComfyClient

client = ComfyClient()   # 默认 127.0.0.1，实例地址按方法分流
```

| 方法 | 引擎 | 端口 | 用途 |
|------|------|------|------|
| `generate_t2v(prompt, seed, width, height, length, steps, cfg)` | Wan2.2 T2V | 8189 | 文生视频 |
| `generate_i2v(prompt, image_path, seed, ...)` | Wan2.2 I2V | 8189 | 参考帧→视频（Wan22ImageToVideoLatent，48ch latent + noise_mask） |
| `generate_tripsplat(image_path, seed)` | TripoSplat | 8192 | 单图→3DGS PLY |
| `generate_character_anchor(image_path, seed)` | TripoSplat | 8192 | 角色图→3DGS 角色锚 |
| `generate_flux_t2i(prompt, seed)` | FLUX.1-dev | 8192 | 文生图（角色/场景参考帧） |
| `generate_h3_ref2v(prompt, ref_image_paths, ...)` | MiniMax-H3 | 8188 | 参考图→视频+原生音频（v2.5 custom 主力） |

通用方法：`wait_ready(timeout=120)` / `queue_prompt(workflow)` / `upload_image(path)` / `download_file(...)`。

> **注意**：
> - FLUX (8192) 已停，用 `generate_flux_t2i`/`generate_tripsplat` 前需重启（TripoSplat 随 FLUX 共实例）
> - 每镜随机 seed（防 ComfyUI 缓存命中秒出旧结果）
> - workflow 模板在 `utils/workflows/`（wan_t2v.json / wan_i2v.json / triposplat.json / flux_t2i.json）

---

## 3DGS 渲染器（utils/splat_renderer.py）

3DGS PLY → 多角度渲染 PNG，v1 角色锚的核心："每镜头起始帧由同一 3DGS 按镜头角度渲染，喂 I2V 生成，避免跨镜头角色漂移"。纯 CPU（comfy.ldm.triposplat.preview 模块），全程 headless 可 API 调用。

### CLI

```bash
# 8 角度渲染
python utils/splat_renderer.py output/assets/char.ply -o ./renders -n 8 --size 1024

# 带背景合成（角色抠像 → 缩放 → 接地）
python utils/splat_renderer.py char.ply -o ./renders -n 4 --size 1024 --bg scene.png --pitch 15
```

### Python API

```python
from utils.splat_renderer import (
    load_ply, render_splat_at_angle, render_splat_angles,
    composite_bg, auto_frame_distance,
)
```

| 函数 | 说明 |
|------|------|
| `load_ply(ply_path)` | 加载 PLY → `(xyz, rgb, scale, opacity)`；内置 PCA 自动对齐角色主轴到垂直 + 飞点过滤（孤立高斯剔除） |
| `render_splat_at_angle(ply_path, yaw, pitch, output_path, bg_image=None, width=832, height=480, fov=35.0, min_px=5, gain=3.0, supersample=2, char_height_ratio=0.55, ground_ratio=0.88)` | 按角度渲染单帧；传 `bg_image` 时自动抠像合成（角色缩放至 55% 画面高、脚踩 88% 位置接地） |
| `render_splat_angles(ply_path, angles=8, output_dir, size=1024)` | 环绕 8 角度批量渲染 |
| `composite_bg(character_img, bg_path, width, height, ...)` | 角色图 + 背景图 alpha 混合（实现"角色走入场景"） |
| `auto_frame_distance(xyz, fov=35.0)` | 自动计算相机距离（按 3DGS 包围盒） |

### v1.1 修复要点（渲染参数）

| 修复 | 内容 |
|------|------|
| 位姿 | PCA 自动对齐主轴到垂直（替代手动 Y/Z 交换），Z-alignment 0.774→1.000 |
| 接地 | crop 角色 bbox → scale 55% 画面高 → ground 88% 位置（脚踩地不悬浮） |
| 飞点 | `_filter_floaters`：距离 >99th pct + opacity<0.03 的孤立高斯剔除 |
| 平滑 | min_px 3→5、gain 2→3、supersample=2（2x 渲染 → LANCZOS 缩放） |

### 角色锚完整流程（v1）

```
FLUX 生角色参考图 (1024×1024)
  → generate_tripsplat / generate_character_anchor → 3DGS PLY
  → render_splat_at_angle(yaw=每镜角度, bg_image=FLUX 场景图) → 合成参考帧
  → generate_i2v(prompt, 参考帧) → 镜头视频
```

---

## 服务依赖

| 操作 | 需要的服务 | 端口 |
|------|-----------|------|
| generate_t2v / generate_i2v | Wan (GPU2) | 8189 |
| generate_flux_t2i / generate_tripsplat | FLUX+TripoSplat（已停，需重启） | 8192 |
| generate_h3_ref2v | MiniMax-H3 (GPU0) | 8188 |
| splat_renderer | 无（纯 CPU） | — |
