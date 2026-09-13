# Vidance v3 使用指南 — 3D 重建与资产库小工具

> v3 "2D→3D→新视角" 相关的小工具：Hunyuan3Dv2 mesh 重建、pyrender mesh 渲染、资产库管理。
>
> 主流程见 [usage.md](./usage.md)，其他版本：[usage-v1.md](./usage-v1.md) / [usage-v2.md](./usage-v2.md) / [usage-v4.md](./usage-v4.md)

## 总览

| 工具 | 一句话 | 服务依赖 |
|------|--------|---------|
| `utils/hunyuan3d.py` | Hunyuan3Dv2 mesh 重建客户端 | Hunyuan3Dv2 :8193 (GPU1) |
| `utils/mesh_render.py` | GLB mesh 多角度渲染（pyrender EGL） | 无（GPU/EGL） |
| `utils/asset_registry.py` | 角色/场景资产库（registry.json） | 无 |

---

## Hunyuan3D 客户端（utils/hunyuan3d.py）

Hunyuan3Dv2 turbo：图→mesh 重建，4 步一致性蒸馏 ~61s/图（vs 标准 50 步 ~10min）。

### Python API

```python
from utils.hunyuan3d import Hunyuan3DClient

client = Hunyuan3DClient()   # 连 :8193（GPU1 独立实例）
```

| 方法 | 说明 |
|------|------|
| `generate_single(image_path, seed=None, ...)` | 单图 → GLB（doubao.jpg → 257K vertices，~61s，9.7MB） |
| `generate_multiview(front_path, left_path, back_path, ...)` | 三视图 → 更准 GLB（可选，单图已够用） |

### 引擎细节

| 组件 | 位置 | 大小 |
|------|------|------|
| Hunyuan3D-DiT-v2-0-turbo | `ComfyUI/models/diffusion_models/` | 4.6G fp16 |
| Hunyuan3D-VAE-v2-0 | `ComfyUI/models/vae/` | 409M fp16 |
| DINOv2-giant | `ComfyUI/models/clip_vision/`（从 DiT checkpoint 提取） | 2.27G |

> workflow 模板：`utils/workflows/hunyuan3d.json`（single-image）

---

## Mesh 渲染器（utils/mesh_render.py）

GLB mesh → 多角度渲染 PNG，与 `splat_renderer` API 对齐（便于 pipeline 一键切换 mesh/3DGS 双路径）。pyrender EGL headless 渲染。

### CLI

```bash
# 8 角度环绕渲染
python utils/mesh_render.py model.glb -o ./renders -n 8 --size 1024

# 带背景合成 + 贴图
python utils/mesh_render.py model.glb -o ./renders -n 4 --bg scene.png --texture ref.png
```

### Python API

```python
from utils.mesh_render import (
    load_mesh, render_mesh_at_angle, render_mesh_angles,
    render_mesh_view, composite_bg, compute_camera_pose, auto_frame_distance,
)
```

| 函数 | 说明 |
|------|------|
| `load_mesh(glb_path, normalize=True, gray_fallback=False, texture_image=None)` | 加载 GLB → trimesh；可选投影参考图贴色（`_project_texture`）、灰度 fallback |
| `render_mesh_at_angle(glb_path, yaw, pitch, output_path, bg_image=None, width=832, height=480, fov=35.0, key_intensity=3.0, fill_intensity=1.0, char_height_ratio=0.55, ground_ratio=0.88)` | **简化接口**：按 yaw/pitch 渲染单帧 + 可选背景合成（与 splat_renderer.render_splat_at_angle 签名一致） |
| `render_mesh_angles(glb_path, angles=8, output_dir, size=1024, pitch=15.0, ...)` | 环绕多角度批量渲染 |
| `render_mesh_view(glb_path, camera, output_path, ...)` | 完整接口：camera dict `{yaw, pitch, fov}` |
| `compute_camera_pose(yaw=0.0, pitch=0.0, distance=2.0, look_at=(0,0,0))` | 相机位姿计算 |
| `auto_frame_distance(mesh, fov=35.0, margin=1.8)` | 按 mesh 包围盒自动取距 |
| `composite_bg(char_color, char_alpha, bg_path, width, height, ...)` | 角色+背景合成 |

### 与 splat_renderer 对齐

| | splat_renderer (v1) | mesh_render (v3) |
|--|--------------------|------------------|
| 输入 | TripoSplat .ply（3DGS） | Hunyuan3Dv2 .glb（mesh） |
| 渲染 | comfy.triposplat.preview（CPU） | pyrender EGL（GPU） |
| 简化接口 | `render_splat_at_angle(ply, yaw, pitch, out, bg_image=...)` | `render_mesh_at_angle(glb, yaw, pitch, out, bg_image=...)`（同签名） |

> **环境要求**：`PYOPENGL_PLATFORM=egl` 必须在 import pyrender 前设置（headless 服务器无 X）；光照 key 3.0 + fill 1.0（两面打光防背面全黑）

---

## 资产库（utils/asset_registry.py）

角色/场景资产入库 + 按描述语义检索（difflib 序列相似度），v3 M7 端到端联调时入阈值 score≥7 资产。

### 存储

- 位置：`{output_dir}/assets/registry.json` + `assets/mesh/`（GLB 落盘）
- `output_dir` 从 config.json 读取（默认 `/mnt/dataset/zxy/vidance/output`）

### CLI

```bash
# 列出全部资产
python utils/asset_registry.py list --type characters   # 或 scenes
# 按描述检索
python utils/asset_registry.py find "一只橘猫" --type characters --min-sim 0.5
# 资产统计
python utils/asset_registry.py stats
```

### Python API

```python
from utils.asset_registry import AssetRegistry

reg = AssetRegistry()
```

| 方法 | 说明 |
|------|------|
| `register_character(id, desc, path, ...)` | 角色入库（id + 描述 + GLB 路径 + 元数据） |
| `find_character(desc, min_similarity=0.5, ...) -> dict | None` | 按描述检索最相似角色（SequenceMatcher 相似度 ≥ 阈值才返回） |
| `get_character(id) -> dict | None` | 按 id 精确取 |
| `list_characters() -> list` | 列出全部角色 |
| `register_scene(id, desc, glb_path, ...)` / `find_scene(desc, ...)` / `get_scene(id)` / `list_scenes()` | 场景同上 |
| `stats() -> dict` | 资产统计（数量等） |

> **注意**：检索是字符串序列相似度（`difflib.SequenceMatcher`），非向量语义检索——描述措辞相近才命中，中文长描述建议 min_similarity 0.3-0.4

---

## v3 完整流程（mesh 路径）

```
FLUX 生角色参考图
  → Hunyuan3DClient.generate_single → GLB (257K verts)
  → render_mesh_at_angle(glb, yaw=每镜角度, bg_image=场景图) → 合成参考帧
  → Wan I2V → 镜头视频 → RIFE 过渡 → LUT/BGM → compose 成片
  → AssetRegistry.register_character（score≥7 入库，下次 find_character 复用）
```
