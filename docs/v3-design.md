# Vidance v3 设计文档 — 2D→3D→新视角

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v1/v2 见 [v1-design.md](./v1-design.md)/[v2-design.md](./v2-design.md)
>
> **状态：🚧 实现中**（M0+M1+M2+M3+M4+M5+M6+M7 完成，M8 待开始）
>
> **M0 headless 渲染验证** ✅：trimesh + pyrender + EGL 后端，box/icosphere GLB 渲染成功
> **M1 Hunyuan3Dv2 mesh 重建** ✅：DiT turbo + VAE + DINOv2-giant 提取，ComfyUI workflow 验证通过

## 1. 概述

### 1.2 v2 遗留问题

v1/v2 用 3DGS 做角色锚，RenderSplat 渲染各角度参考帧 —— 但这只解决"角色"的一致性：
- **场景仍是 2D**：每镜背景由 FLUX 独立生成，跨镜头场景（如"同一间屋子从不同角度拍"）不一致
- **视角受限**：RenderSplat 只能绕 3DGS 轨道运动（yaw/pitch/fov），无法生成"角色走进一条街，镜头从街角切到街尾"这种场景级视角变换
- **资产不可导出**：3DGS 是渲染中间态，无法导出为可复用 3D 资产（GLB/OBJ）

### 1.3 v3 目标

从 2D 内容重建 3D mesh 资产，生成原视角没有的新角度视频：

- **场景 3D 重建**：FLUX 生成场景图 → Hunyuan3Dv2 重建 mesh → headless 渲染新视角
- **角色 mesh 化**：除 3DGS 外，补 mesh 路径（可导出 GLB，可上材质/灯光/动画）
- **多视角生成**：mesh 渲染任意视角参考帧 → Wan I2V 生成动态
- **资产复用**：同一场景/角色 mesh 跨任务复用，积累资产库

### 1.4 v3 范围

| 做 | 不做（留给后续版本） |
|----|---------------------|
| Hunyuan3Dv2 单图→mesh 重建 | mesh 动画/骨骼绑定（需 Blender/专业工具） |
| Hunyuan3Dv2 多视角条件（front/left/back/right） | 实时光流驱动 mesh 变形 |
| headless mesh 渲染新视角（pyrender+EGL） | 物理仿真 |
| mesh → GLB 导出（SaveGLB） | 4D 重建（视频→动态3D） |
| mesh 资产库管理 | 爬热点素材（v4） |
| mesh 渲染参考帧 → Wan I2V | 异步任务制（v4） |
| 3DGS ↔ mesh 双路径（角色可选） | |

### 1.5 核心验证点

1. ✅ Hunyuan3Dv2 单图→mesh 质量（M1 验证：257K verts, 591K faces，几何完整）
2. ✅ 多视角条件（3 图）优于单图重建（M2 验证：aspect 1.02→1.69, 主分量 38%→63%）
3. ✅ **headless mesh 渲染方案能否跑通**（M0 验证：pyrender+EGL 成功，见 §10）
4. mesh 渲染新视角 → Wan I2V，角色/场景一致性是否优于纯 3DGS（M4+ 验证）
5. GLB 资产导出与跨任务复用可行性（M1 已导出 GLB，M6 验证复用）
6. 3DGS（v1）与 mesh（v3）双路径适用场景区分（M5 验证）

---

## 2. 架构设计

### 2.1 v2 → v3 架构演进

```
v2:  角色锚(3DGS) → 逐镜(RenderSplat → Wan I2V) → 后期
     场景仍是 2D（FLUX 每镜独立生成）

v3:  [角色: FLUX → TripoSplat→3DGS  |  FLUX → Hunyuan3D→mesh]  双路径
     [场景: FLUX → Hunyuan3D→场景mesh → headless 渲染新视角]
     → 逐镜: (角色mesh + 场景mesh) 合成视角参考帧 → Wan I2V → 后期
```

### 2.2 三层架构（v3）

```
┌──────────────────────────────────────────────────────────┐
│  产品层   v3: 2D→3D→新视角（场景级 3D 一致性）            │
├──────────────────────────────────────────────────────────┤
│  编排层   opencode agent runtime                          │
│           director + subagents(reviewer/asset) + skills   │
│           core/ pipeline（角色+场景双锚 + mesh 渲染）      │
├──────────────────────────────────────────────────────────┤
│  引擎层   角色: FLUX → TripoSplat(3DGS) / Hunyuan3D(mesh) │
│           场景: FLUX → Hunyuan3D(场景mesh)                │
│           渲染: pyrender+EGL(headless) + RenderSplat       │
│           视频: Wan I2V (8189)                             │
│           音频: edge-tts + CosyVoice (9880)                │
│           后期: RIFE + ffmpeg (复用 v2)                    │
│           LLM: USTC API                                   │
└──────────────────────────────────────────────────────────┘
```

### 2.3 混合模式（v3 扩展）

| 能力 | 类型 | 归属 |
|------|------|------|
| 分镜（含每镜场景视角 + 角色视角） | 创意决策 | **agent** |
| 角色/场景是否用 mesh 路径（质量需求判断） | 创意决策 | **agent** |
| 多视角重建需要哪几个视角图 | 创意决策 | **agent** |
| mesh 资产入库/检索复用决策 | 创意决策 | **agent**（asset subagent） |
| FLUX 生成多视角图 | 确定性执行 | **code** |
| Hunyuan3Dv2 重建 mesh | 确定性执行 | **code** |
| headless mesh 渲染新视角 | 确定性执行 | **code** |
| 3DGS + mesh 合成参考帧 | 确定性执行 | **code** |
| Wan I2V / TTS / 后期 | 确定性执行 | **code**（复用 v1/v2） |
| mesh 质量审查 | 多模态判断 | **agent** |

### 2.4 agent 拓扑（v3）

```
director (主控 agent)
  │
  ├─ [LLM] 编剧：concept → script（含每镜 scene_camera + char_camera）
  ├─ [LLM] 决策：角色/场景走 3DGS 还是 mesh（按质量需求）
  │
  ├─ [角色锚阶段]
  │   ├─ 路径A (3DGS, 复用 v1): FLUX → TripoSplat → character.splat
  │   └─ 路径B (mesh, v3新): FLUX多视角 → Hunyuan3D → character.glb
  │        └─ task → asset subagent: mesh 入库（character_id）
  │
  ├─ [场景锚阶段] (v3 新增，全片每场景一次)
  │   ├─ [LLM] 场景描述 → 多视角 prompt（front/side/back）
  │   ├─ [FLUX] 生成场景多视角图
  │   ├─ [Hunyuan3D] 场景图 → scene_mesh.glb
  │   ├─ [headless 渲染] scene_mesh 按镜头视角 → scene_bg_{id}.png
  │   └─ task → asset subagent: 场景 mesh 入库（scene_id）
  │
  ├─ [逐镜阶段] (复用 v1/v2，并行)
  │   ├─ [角色渲染] 3DGS(RenderSplat) 或 mesh(headless) → char_frame
  │   ├─ [场景渲染] scene_mesh headless → scene_bg
  │   ├─ [合成] char_frame + scene_bg → composite_ref
  │   ├─ [Wan I2V] → shot.mp4
  │   └─ [TTS + 审片]
  │
  └─ [后期] (复用 v2: RIFE + 调色 + 配乐 + 字幕)
```

---

## 3. 核心流程：3D 重建与渲染

### 3.1 角色双路径

```
[决策] 角色质量需求？
  ├─ 高（特写/主角）→ mesh 路径（Hunyuan3D，几何准、可导出）
  └─ 标准（远景/配角）→ 3DGS 路径（TripoSplat，快、v1 已验证）

mesh 路径:
  [FLUX] 生成角色多视角图（front + left + right + back）
    → [CLIPVisionEncode] × 4
    → [Hunyuan3Dv2ConditioningMultiView] (front/left/back/right 带位置编码)
    → [EmptyLatentHunyuan3Dv2] (resolution=3072)
    → [KSampler] (Hunyuan3Dv2 模型)
    → [VAEDecodeHunyuan3D] (octree_resolution=256)
    → [VoxelToMesh] (algorithm="surface net")
    → [SaveGLB] → character.glb
```

> **多视角条件**优于单图：`Hunyuan3Dv2ConditioningMultiView` 对 4 视角 embeds 加 1D sincos 位置编码后拼接，重建背面更完整。v1 的 TripoSplat 只吃单图。

### 3.2 场景 3D 重建

```
[LLM] 场景描述 → 多视角 prompt（如"雪原木屋"front/side/back）
  → [FLUX] × 3 视角图
  → [Hunyuan3D] → scene_mesh.glb
  → [headless 渲染] 按每镜 scene_camera 渲染 → scene_bg_{id}.png
```

场景 mesh 与角色 mesh 不同：
- 角色 mesh 需要精细几何（特写），用多视角 + 高 resolution
- 场景 mesh 可降级（远景），单图重建 + 低 resolution 也可接受

### 3.3 headless mesh 渲染（✅ M0 已解决）

~~这是 v3 最大技术风险。~~ **M0 验证通过**：trimesh + pyrender + EGL 后端在 4×4090 服务器 headless 渲染 GLB→IMAGE 完全可行。

ComfyUI 现状：
- `SaveGLB`：纯 Python 写 GLB 文件 ✅ headless OK
- `Save3DAdvanced` / `SaveGaussianSplat`：需 `viewport_state`（来自 Load3D 浏览器节点）❌ 无法 API 调用
- **无纯 Python "mesh + camera → IMAGE" 节点** → 用 pyrender 替代（✅ M3 已封装 `utils/mesh_render.py`）

选定方案（见 §10 详述）：
1. **trimesh + pyrender + EGL**（✅ 已选定）：纯 Python，OpenGL 离屏渲染，headless，NVIDIA 驱动直接支持

### 3.4 视角合成

```
[角色渲染] 3DGS(RenderSplat, char_camera) 或 mesh(headless, char_camera) → char.png (含alpha)
[场景渲染] scene_mesh headless, scene_camera → scene_bg.png (无角色)
[合成] char.png alpha 混合到 scene_bg.png → composite_ref.png
[Wan I2V] composite_ref + video_prompt → shot.mp4
```

与 v1 区别：v1 场景是 FLUX 独立 2D 图；v3 场景是 mesh 渲染，跨镜头场景视角一致。

---

## 4. 数据模型（v3 扩展）

### 4.1 分镜脚本 schema（v3 新增）

```json
{
  "character": {
    "path": "mesh",               // v3 新增："3dgs" | "mesh"
    "ref_images": ["front.png", "left.png", "back.png", "right.png"],  // mesh 路径多视角
    "splat": "character.splat",   // 3dgs 路径
    "mesh": "character.glb"       // mesh 路径
  },
  "scenes": [                     // v3 新增：场景资产
    {
      "id": "snow_village",
      "desc": "雪原中的木屋村庄",
      "ref_images": ["front.png", "side.png", "back.png"],
      "mesh": "scenes/snow_village.glb"
    }
  ],
  "shots": [
    {
      "id": 1,
      "scene_id": "snow_village",       // v3 新增：引用场景资产
      "char_camera": { "yaw": 30, "pitch": 5, "fov": 50 },   // 角色视角
      "scene_camera": { "yaw": 60, "pitch": -10, "fov": 35, "position": [2, 1.5, -3] },  // v3 新增：场景视角
      ...
    }
  ]
}
```

### 4.2 资产库 schema（v3 新增）

```json
// assets/registry.json
{
  "characters": [
    {
      "id": "red_cloak_boy",
      "path": "mesh",
      "glb": "assets/mesh/red_cloak_boy.glb",
      "splat": null,
      "desc": "红斗篷短发少年",
      "created": "2026-09-10",
      "quality_score": 8
    }
  ],
  "scenes": [
    {
      "id": "snow_village",
      "glb": "assets/mesh/snow_village.glb",
      "desc": "雪原木屋村庄",
      "created": "2026-09-10"
    }
  ]
}
```

---

## 5. 引擎接口规格

### 5.1 Hunyuan3Dv2 重建（ComfyUI，✅ M1 已验证）

单图路径（M1 验证通过，`workflows/hunyuan3d_single.json`）：
```
LoadImage → CLIPVisionLoader(hunyuan3d_dinov2_giant) → CLIPVisionEncode
  → Hunyuan3Dv2Conditioning(positive/negative) → KSampler
EmptyLatentHunyuan3Dv2(resolution=3072) ──────────────→  ↓
UNETLoader(hunyuan3d-dit-v2-0-turbo) ────────────────→  ↓
KSampler(4步, euler, simple, cfg=1.0) → VAEDecodeHunyuan3D(num_chunks=8000, octree_resolution=256)
  → VoxelToMesh("surface net", threshold=0.6) → SaveGLB
```

**M1 实测参数**：GPU1:8193，~61s 执行，输出 9.7MB GLB（257K verts, 591K faces）

多视角路径（M2 验证通过，`workflows/hunyuan3d_multiview.json`）：
```
LoadImage×4 (front/left/back/right) → CLIPVisionEncode×4
  → Hunyuan3Dv2ConditioningMultiView → KSampler → ... → SaveGLB
```

节点（源码 `comfy_extras/nodes_hunyuan3d.py`）：
| class_type | 作用 |
|-----------|------|
| `EmptyLatentHunyuan3Dv2` | 3D latent（[B,64,resolution]） |
| `Hunyuan3Dv2Conditioning` | 单图条件（clip_vision → positive/negative） |
| `Hunyuan3Dv2ConditioningMultiView` | 多视角条件（4图带位置编码拼接） |
| `VAEDecodeHunyuan3D` | latent → voxel（num_chunks/octree_resolution） |
| `VoxelToMesh` | voxel → mesh（"surface net" 优于 "basic"） |
| `SaveGLB` | mesh → GLB（纯 Python，支持 UV/顶点色/纹理） |

模型文件（✅ 已下载）：
| 文件 | 目录 | 大小 | 说明 |
|------|------|------|------|
| hunyuan3d-dit-v2-0-turbo.fp16 | diffusion_models/ | 4.6G | 4步一致性蒸馏 DiT |
| hunyuan3d-vae-v2-0.fp16 | vae/ | 409M | mesh VAE |
| hunyuan3d_dinov2_giant | clip_vision/ | 2.27G | DINOv2-giant（从 DiT 提取） |

### 5.2 headless mesh 渲染（✅ M0 验证 + M3 封装完成）

接口设计（`utils/mesh_render.py`，✅ M3 已封装）：
```python
# utils/mesh_render.py
def render_mesh_view(mesh_path: str, camera: dict, output_path: str,
                     width=1280, height=704, bg_image=None) -> str:
    """渲染 mesh 指定视角 → IMAGE，可选合成背景"""
    # camera: {yaw, pitch, fov, distance, look_at, position}
    # 底层：pyrender.OffscreenRenderer + EGL 后端
    # 返回 output_path

def render_mesh_at_angle(glb_path, yaw, pitch, output_path, bg_image=None,
                         width=832, height=480, fov=35.0) -> str:
    """简化接口，与 splat_renderer.render_splat_at_angle API 对齐"""

def render_mesh_angles(glb_path, angles=8, output_dir='.', size=1024, ...) -> list[str]:
    """批量多角度渲染，与 splat_renderer.render_splat_angles API 对齐"""
```

### 5.3 复用 v1/v2 组件

3DGS 路径（TripoSplat/RenderSplat）、Wan I2V、TTS、RIFE/ffmpeg 后期 —— 全部复用。

---

## 6. GPU 与显存规划

### 6.1 v3 GPU 分配（实际）

| GPU | 服务 | 显存 | v3 变化 |
|-----|------|------|---------|
| GPU0 | H3(8188) | 46G | custom 模式主力，模型按需加载 |
| GPU1 | Hunyuan3Dv2(8193) | ~15G | **v3 独立实例**（从 8189 分离） |
| GPU2 | Wan I2V(8189) + TTS(9880) | 17.8G + 2.5G | Wan 独占（Hunyuan3Dv2 已移走） |
| GPU3 | HunyuanVideo(8190) | 20G | 备选 T2V |

- Hunyuan3Dv2 独立 GPU1:8193，与 Wan 视频生成可并行（不再串行排队）
- mesh 渲染（pyrender+EGL）用 GPU 离屏渲染，显存占用极小（~1-2G）
- 多视角重建显存：4 图 CLIP encode + 3D latent，估 ~15-20G（M2 验证）

---

## 7. Workflow 模板清单（v3 新增）

| 模板 | 用途 | 状态 |
|------|------|------|
| `workflows/hunyuan3d_single.json` | 单图→mesh | ✅ M1 验证通过 |
| `workflows/hunyuan3d_multiview.json` | 多视角→mesh | ✅ M2 验证通过 |
| ~~`workflows/mesh_render.json`~~ | ~~mesh→视角图（若自写节点方案）~~ | 不需要，用 `utils/mesh_render.py` (pyrender) |

---

## 8. asset subagent（新增）

```
director → task → asset subagent
  职责:
    - mesh 质量审查（多视角渲染预览 → reviewer 审）
    - 资产入库（character/scene → registry.json）
    - 资产检索（新任务先查库，已有可复用 mesh 则跳过重建）
  不做: 不生成 mesh（那是 code 的 Hunyuan3D 流程）
```

---

## 9. 依赖与前置

### 9.1 新增模型

| 模型 | 用途 | 来源 | 大小 | 状态 |
|------|------|------|------|------|
| Hunyuan3D-DiT-v2-0-turbo | 图→mesh 重建（4步蒸馏） | hf-mirror | 4.6G | ✅ 已下载 |
| Hunyuan3D-VAE-v2-0 | mesh VAE 解码 | hf-mirror | 409M | ✅ 已下载 |
| DINOv2-giant | 图像编码器（从 DiT 提取） | DiT checkpoint | 2.27G | ✅ 已提取 |

> 模型位置：`ComfyUI/models/diffusion_models/`、`ComfyUI/models/vae/`、`ComfyUI/models/clip_vision/`
> DINOv2-giant 提取方式：prefix `conditioner.main_image_encoder.model.` 去前缀，检测键 `encoder.layer.39.layer_scale2.lambda1` 确认

### 9.2 headless 渲染依赖（✅ 已安装）

| 依赖 | 版本 | 状态 |
|------|------|------|
| trimesh | 5.1.0 | ✅ |
| pyrender | 0.1.45 | ✅ |
| PyOpenGL | 3.1.0 | ✅ |
| EGL 后端（NVIDIA 驱动） | 580.173.02 | ✅ |

### 9.3 代码新增

| 文件 | 改动 | 状态 |
|------|------|------|
| `core/pipeline.py` | 角色双路径 + 场景锚阶段 + 资产库集成 | M4-M7 已改 |
| `utils/mesh_render.py` | headless mesh 渲染（pyrender+EGL） | ✅ M3 已封装 |
| `utils/hunyuan3d.py` | Hunyuan3Dv2 重建客户端 | ✅ M5 已封装 |
| `utils/workflows/hunyuan3d_*.json` | 新 workflow | single ✅ / multiview ✅ |
| `utils/asset_registry.py` | 资产库管理（CRUD+模糊搜索+CLI） | ✅ M6 已建 |
| `.opencode/agents/asset.md` | asset subagent | ✅ M6 已建 |

---

## 10. headless 渲染方案（✅ 已解决）

> ~~这是 v3 能否启动的**前置条件**。当前 ComfyUI 无法 headless 渲染 mesh 到图像。~~
> **M0 验证通过**：trimesh + pyrender + EGL 后端在 4×4090 服务器 headless 渲染完全可行，不需要 OSMesa / Blender。

### 10.1 方案对比

| 方案 | 质量 | 部署复杂度 | headless | 纹理/灯光 | 状态 |
|------|------|-----------|----------|----------|------|
| **trimesh + pyrender** | 中 | 低（pip） | ✅（EGL） | 基础 | **✅ 选定** |
| **Blender headless** | 高 | 中（装 Blender） | ✅（`-b`） | 完整 | 备选 |
| **Isaac Sim** | 最高 | 高（NVIDIA 仿真栈） | ✅ | 完整 | 过重，不考虑 |
| **自写 ComfyUI 节点** | 取决于内核 | 中 | ✅ | 取决于内核 | 中期 |

### 10.2 trimesh + pyrender + EGL 路线（已验证）

```bash
pip install trimesh pyrender PyOpenGL
# EGL 后端（NVIDIA GPU，无需 OSMesa / libosmesa6-dev）
export PYOPENGL_PLATFORM=egl
```

```python
# utils/mesh_render.py（✅ M3 已封装，实际实现见源文件）
import os
os.environ['PYOPENGL_PLATFORM'] = 'egl'
import trimesh, pyrender
def render_view(glb_path, camera, out_path, w, h):
    mesh = trimesh.load(glb_path)
    pr = pyrender.OffscreenRenderer(w, h)
    scene = pyrender.Scene()
    scene.add(pyrender.Mesh.from_trimesh(mesh))
    cam = pyrender.PerspectiveCamera(yfov=radians(camera['fov']))
    scene.add(cam, pose=compute_pose(camera))
    color, depth = pr.render(scene)
    alpha = (depth > 0).astype(uint8) * 255
    Image.fromarray(dstack([color, alpha]), 'RGBA').save(out_path)
    pr.delete()  # 释放 EGL 上下文
```

**验证结果**（M0）：
- 环境：trimesh 5.1.0 + pyrender 0.1.45 + PyOpenGL 3.1.0
- EGL 后端：NVIDIA 驱动 580.173.02，`10_nvidia.json` + `50_mesa.json` vendor 配置
- box/icosphere GLB 渲染成功（color + depth 均正常）
- Hunyuan3Dv2 输出 GLB（257K verts）4 角度预览渲染成功（M1 验证）

**注意事项**：
- `os.environ['PYOPENGL_PLATFORM'] = 'egl'` 必须在 import pyrender 之前设置
- `renderer.delete()` 用完释放，避免 EGL 上下文泄漏
- ~~GLB 的 PBR 材质可能部分丢失，需手动加灯光（M3 封装时处理）~~ ✅ M3 已处理：3-point lighting（key+fill+ambient），`gray_fallback` 选项可覆盖材质为灰色

---

## 11. 验收标准

v3 跑通的标志：

1. **headless 渲染**：GLB → 任意视角 IMAGE，headless 可 API 调用（✅ M0 已解决）
2. **角色 mesh**：Hunyuan3Dv2 重建 → GLB，多角度预览审查通过（✅ 单图 M1 + 多视角 M2 验证）
3. **场景 mesh**：场景图 → GLB → 多镜头视角渲染，场景跨镜头一致
4. **双路径**：角色可选 3DGS 或 mesh，agent 按质量需求决策（✅ M5 已完成，`--character-mode mesh/3dgs/flux/auto`）
5. **资产库**：character/scene mesh 入库，跨任务可检索复用
6. **视角一致性**：同场景多镜头，场景结构一致（优于 v2 的独立 2D 背景）
7. **端到端**：概念 → 含场景一致性的长片，1-3 分钟

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| ~~headless 渲染方案跑不通~~ | ~~v3 阻塞~~ | ✅ M0 已解决：pyrender+EGL 方案验证通过 |
| Hunyuan3Dv2 重建质量不足 | mesh 不可用 | 多视角条件提升；备选保留 3DGS 路径 |
| GLB 纹理/材质在 pyrender 丢失 | 渲染图丢色 | trimesh 顶点色 fallback；或 Blender |
| 多视角图视角不一致（FLUX 各生成各） | 重建扭曲 | FLUX prompt 强约束视角 + 用参考图控制 |
| mesh 渲染慢 | 流水线慢 | mesh 预渲染缓存；资产复用减少重复重建 |
| GPU1 显存（Hunyuan3D + SDXL） | OOM | SDXL 让位；Hunyuan3D 用完释放 |

---

## 13. 实现里程碑

| 阶段 | 内容 | 产出 | 状态 |
|------|------|------|------|
| M0 | **headless 渲染验证**（§10 阻塞解锁） | trimesh+pyrender+EGL 渲染 GLB 成功 | ✅ 完成 |
| M1 | Hunyuan3Dv2 模型下载 + single workflow 验证 | doubao.jpg → 257K verts GLB, ~61s | ✅ 完成 |
| M2 | multiview workflow + 多视角重建质量对比 | 3视角→206K verts GLB, multiview优于单图 | ✅ 完成 |
| M3 | mesh_render 封装 + 视角渲染验证 | 8角度渲染+背景合成验证通过 | ✅ 完成 |
| M4 | 场景锚阶段（场景重建 + 渲染背景） | 2场景FLUX→Hunyuan3D→mesh_render全链路验证通过 | ✅ 完成 |
| M5 | 角色双路径（3DGS/mesh）选择 | 双路径打通 | ✅ 完成 |
| M6 | 资产库 + asset subagent | 资产复用 | ✅ 完成 |
| M7 | pipeline 集成 + 端到端联调 | 闭环 | ✅ 完成 |
| M8 | 场景一致性长片验证 | v3 成片 | 待开始 |

### M0 完成详情（2026-09-11）
- trimesh 5.1.0 + pyrender 0.1.45 + PyOpenGL 3.1.0 安装成功
- EGL 后端（`PYOPENGL_PLATFORM=egl`）在 4×4090 服务器 headless 渲染 GLB→IMAGE 完全可行
- 不需要 OSMesa、不需要 Blender，NVIDIA EGL 驱动 580.173.02 直接支持离屏渲染
- box/icosphere 测试 mesh 渲染正常（color + depth 均输出）

### M1 完成详情（2026-09-11）
- **模型下载**：
  - DiT turbo `hunyuan3d-dit-v2-0-turbo.fp16.safetensors`（4.6GB）→ `models/diffusion_models/`
  - VAE `hunyuan3d-vae-v2-0.fp16.safetensors`（409MB）→ `models/vae/`
  - DINOv2-giant `hunyuan3d_dinov2_giant.safetensors`（2.27GB）→ `models/clip_vision/`（从 DiT checkpoint 提取，prefix `conditioner.main_image_encoder.model.` 去前缀）
- **ComfyUI workflow**（GPU1 port 8193，~61s 执行）：
  - 节点链：LoadImage→CLIPVisionLoader→CLIPVisionEncode→Hunyuan3Dv2Conditioning→EmptyLatentHunyuan3Dv2(3072)→UNETLoader→KSampler(4步,euler,simple,cfg=1.0)→VAEDecodeHunyuan3D(8000chunks,octree=256)→VoxelToMesh(surface net,threshold=0.6)→SaveGLB
- **输出**：`output/hunyuan3d/test_00001_.glb`（9.7MB）
- **mesh 质量**：257K vertices, 591K faces, bbox ~1.96×1.93×0.76, non-watertight
- **pyrender 预览**：4 角度渲染成功（color max=202, depth max=2.75-3.27）

> **M0 是 v3 启动门槛**，已通过。M1 single-image mesh 重建已验证，M2 多视角重建已验证。

### M2 完成详情（2026-09-11）
- **输入准备**：从 doubao.jpg 三视图（1536×1024）裁剪 3 个视角图（front/left/back，各 ~503×1024）
- **ComfyUI workflow**（GPU1 port 8193，~22s warm 执行）：
  - 节点链：3×LoadImage→CLIPVisionLoader→3×CLIPVisionEncode→Hunyuan3Dv2ConditioningMultiView(front,left,back)→EmptyLatentHunyuan3Dv2(3072)→UNETLoader→KSampler(4步)→VAEDecodeHunyuan3D→VoxelToMesh→SaveGLB
- **输出**：`output/hunyuan3d/multiview_test_00001_.glb`（8.41MB）
- **mesh 质量**：206K vertices, 528K faces, bbox 1.16×1.96×0.77, non-watertight
- **pyrender 预览**：4 角度渲染成功
- **质量对比**（M1 single vs M2 multiview）：

  | 指标 | M1 Single | M2 MultiView | 结论 |
  |------|-----------|--------------|------|
  | Verts | 257K | 206K | MultiView 更精简 |
  | Faces | 591K | 528K | MultiView 更精简 |
  | BBox | 1.96×1.93×0.76 | 1.16×1.96×0.77 | MultiView 比例更合理 |
  | Aspect (高/宽) | 1.02 (方形) | 1.69 (高瘦) | **MultiView 角色比例自然** |
  | 主连通分量占比 | 38.1% | 63.0% | **MultiView 网格更连贯** |
  | 总连通分量 | 58556 | 92873 | MultiView 碎片更多但主体更大 |
  | Volume | 0.399 | 0.366 | 接近 |
  | 退化解 | 73 | 74 | 接近 |

- **关键结论**：multiview 重建显著优于单图
  - 侧/背面视角约束了 X 方向宽度（1.96→1.16），角色不再"扁平"
  - 主连通分量占比从 38%→63%，mesh 整体性大幅提升
  - 3 视角（front/left/back）已足够，right 视角可省略（节点支持 optional）
- **workflow 模板**：`workflows/hunyuan3d_single.json` + `workflows/hunyuan3d_multiview.json`
- **对比预览图**：`output/hunyuan3d_comparison.png`（4 角度 × 2 mesh 并排）

### M3 完成详情（2026-09-12）
- **封装文件**：`utils/mesh_render.py`（260 行）
- **三个 API 层级**：
  - `render_mesh_view(mesh_path, camera: dict, ...)` — 高级接口，camera 支持 yaw/pitch/fov/distance/look_at/position
  - `render_mesh_at_angle(glb_path, yaw, pitch, ...)` — 简化接口，与 `splat_renderer.render_splat_at_angle` API 对齐
  - `render_mesh_angles(glb_path, angles=8, ...)` — 批量多角度，与 `splat_renderer.render_splat_angles` API 对齐
- **核心功能**：
  - `load_mesh()` — GLB/GLTF/OBJ/PLY 加载，居中+缩放到单位 bbox，可选灰色材质覆盖
  - `auto_frame_distance()` — 基于 mesh bbox + FOV 自动计算相机距离
  - `compute_camera_pose()` — yaw/pitch/distance → 4×4 camera-to-world 矩阵
  - `_look_at_pose()` — look-at 矩阵（OpenGL 惯例：相机看向 -Z）
  - `_setup_lighting()` — 3-point lighting（key light 跟相机，fill light 对侧，ambient 环境光）
  - `_render_mesh()` — pyrender 离屏渲染，返回 (color, depth, alpha)，alpha 从 depth buffer 生成
  - `composite_bg()` — alpha 混合角色到背景图（裁剪+缩放+接地放置，与 splat_renderer 逻辑一致）
- **与 splat_renderer 的对应关系**：
  | splat_renderer | mesh_render | 说明 |
  |----------------|-------------|------|
  | `render_splat_at_angle()` | `render_mesh_at_angle()` | 同签名，PLY→GLB 切换 |
  | `render_splat_angles()` | `render_mesh_angles()` | 同签名 |
  | `composite_bg()` | `composite_bg()` | 同逻辑，alpha 来源不同（color keying vs depth buffer） |
  | `load_ply()` + PCA 对齐 | `load_mesh()` + normalize | mesh 无需 PCA（Hunyuan3D 输出已对齐） |
  | `auto_frame_distance()` | `auto_frame_distance()` | 同逻辑，基于 99th percentile vs bbox |
- **验证测试**（8 项全通过）：
  - 单角度渲染 832×480（Wan I2V 分辨率）：0.79s，alpha coverage 15.7%
  - 批量 8 角度 512×512：3.41s（~0.43s/张），coverage 18-33%（角度对称）
  - camera dict position 模式：0.40s
  - camera dict yaw/pitch 模式：通过
  - 背景合成（bg=doubao.jpg）：832×480 RGB，pixel range [0,255] mean=216.7
  - gray_fallback 灰色材质：通过
  - M1 vs M2 对比：M2 coverage 33.3% > M1 32.1%，M2 color_mean 74.1 > M1 68.4（更亮更完整）
- **验证图**：`output/m3_mesh_render_overview.png`（8 角度 + 合成预览）、`output/m3_composite_test.png`

### M4 完成详情（2026-09-12）
- **目标**：验证场景锚全链路 FLUX → Hunyuan3Dv2 → mesh_render，场景跨视角一致性
- **测试场景**：
  - Scene A：隔离木屋（白色背景，3D model style）— 最佳重建条件
  - Scene B：雪原木屋（带环境，photorealistic）— 真实使用场景
- **FLUX 生成**（GPU0:8192, 1024×1024, 20 steps）：
  - Scene A: 24s, seed=376359990
  - Scene B: 12s, seed=549398521
- **Hunyuan3Dv2 重建**（GPU1:8193, single-image, 4步 turbo）：
  - Scene A: 238K verts, 605K faces, 60s（冷启动）, bbox 1.0×0.59×0.96（扁平建筑形）
  - Scene B: 411K verts, 911K faces, 27s（预热）, bbox 0.99×0.93×1.0（立方环境形）
- **mesh_render 8 角度渲染**（832×832, fov=35°, pitch=10°）：
  - Scene A: 4.3s, coverage 37-44%（对称一致 → 3D 一致性好）
  - Scene B: 6.3s, coverage 37-60%（不对称 → 环境复杂度高）
- **场景 mesh vs 角色 mesh 对比**：

  | 指标 | Character (M2) | Scene A (isolated) | Scene B (environment) |
  |------|----------------|--------------------|-----------------------|
  | Verts | 206K | 238K | 411K |
  | Faces | 528K | 605K | 911K |
  | BBox | 0.59×1.0×0.39 (高瘦) | 1.0×0.59×0.96 (扁平) | 0.99×0.93×1.0 (立方) |
  | Aspect (h/w) | 1.69 | 0.59 | 0.94 |
  | 重建耗时 | 22s | 60s (冷) | 27s (热) |
  | 8角度覆盖一致性 | — | 高 (37-44%) | 低 (37-60%) |

- **关键结论**：
  - 场景锚全链路 FLUX → Hunyuan3Dv2 → mesh_render 验证通过，无需额外封装
  - 隔离物体（白色背景）重建效果好，角度覆盖一致性高
  - 带环境场景重建可行但几何复杂度高（411K verts），角度覆盖变化大
  - 场景 mesh 比 3DGS 优势：跨镜头视角一致（同一 3D mesh 不同角度渲染），v1 的 FLUX 独立 2D 图无此保证
  - 场景 mesh 可降级：设计文档建议远景可单图+低 resolution，实测单图已足够
- **验证图**：
  - `output/m4_scene_anchor/m4_scene_a_overview.png` — Scene A 原图 + 8 角度渲染
  - `output/m4_scene_anchor/m4_scene_b_overview.png` — Scene B 原图 + 8 角度渲染
  - `output/m4_scene_anchor/scene_a_cabin.png` / `scene_b_snowy_cabin.png` — FLUX 原图
  - `output/m4_scene_anchor/scene_a_cabin.glb` / `scene_b_snowy_cabin.glb` — 重建 mesh

### M5 完成详情（2026-09-12）
- **目标**：角色双路径打通 — `--character-mode mesh` 可用，Hunyuan3Dv2 重建 + mesh_render 渲染接入 pipeline
- **新增代码**：
  - `utils/hunyuan3d.py`（95 行）— Hunyuan3Dv2 ComfyUI 客户端，`generate_single`(单图→GLB) + `generate_multiview`(3视角→GLB)，使用 `ComfyClient(instance='hunyuan3d')` → GPU1:8193
  - `core/pipeline.py` — `_build_character_anchor` 新增 mesh 分支（FLUX→Hunyuan3Dv2→GLB→mesh_render 预览审查），`_generate_scene_ref` 新增 mesh 分支（render_mesh_at_angle + composite_bg），auto 降级逻辑兼容 mesh
  - `core/vidance.py` — `--character-mode` choices 增加 `'mesh'`
- **测试**（`/tmp/opencode/test_m5_dual_path.py`）：
  - Hunyuan3DClient.generate_single: doubao.jpg → 282K verts GLB, 28.8s（热启动）
  - render_mesh_angles: 4 角度预览 3.3s, coverage 20-35%
  - render_mesh_at_angle + composite_bg: 832×480 合成 0.66s, 7.9% foreground
- **验证图**：
  - `output/m5_dual_path/character.glb` — 重建 mesh
  - `output/m5_dual_path/previews/` — 4 角度预览
  - `output/m5_dual_path/composite_ref_test.png` — 合成参考帧
- **character-mode 四模式**：
  - `auto`（默认）— 先试 3DGS，审查不过降级 flux
  - `3dgs` — FLUX → TripoSplat → 3DGS → splat_renderer（v1 路径）
  - `mesh` — FLUX → Hunyuan3Dv2 → GLB → mesh_render（v3 新路径）
  - `flux` — 每镜 FLUX 直接生成，不重建 3D

### M6 完成详情（2026-09-12）
- **目标**：角色/场景 3D 资产库管理，跨任务复用 mesh，减少重复重建
- **新增代码**：
  - `utils/asset_registry.py`（237 行）— `AssetRegistry` 类，CRUD + 模糊搜索 + CLI
    - `register_character(id, desc, path, glb_path/splat_path, ref_image, quality_score)` — 自动复制文件到 assets 目录
    - `find_character(desc, min_similarity=0.5, path_filter='mesh'|'3dgs')` — SequenceMatcher 模糊匹配 + path 过滤
    - `register_scene(id, desc, glb_path, ref_image)` / `find_scene(desc)` — 场景资产
    - CLI: `python utils/asset_registry.py {list|find|stats}`
  - `.opencode/agents/asset.md` — asset subagent 定义
  - `core/pipeline.py` — 资产库集成：
    - `__init__` 新增 `self.assets = AssetRegistry(config)`
    - `_build_character_anchor` 新增 `[3-pre]` 资产检索（mesh/3dgs 模式，相似度≥0.6 + score≥7 时复用，跳过重建）
    - `_build_character_anchor` 新增 `[3d]` 资产入库（review score≥7 时自动注册）
    - `run()` 新增 `no_asset_reuse` 参数
  - `core/vidance.py` — `--no-asset-reuse` CLI flag
- **资产 schema**（§4.2 已定义）：`registry.json` 存 `output/assets/`，mesh 文件存 `output/assets/mesh/`
- **复用判断规则**：
  - 描述相似度 ≥ 0.6（SequenceMatcher ratio）
  - quality_score ≥ 7
  - `path` 字段匹配需求（mesh vs 3dgs）
  - `--no-asset-reuse` 可跳过检索
- **测试**（8 项 CRUD + 6 项集成，全通过）：
  - register_character + register_scene（文件复制到 assets/mesh/）
  - find_character 精确匹配 + 模糊匹配 + 无匹配
  - find_scene 模糊匹配
  - 更新已有条目（quality_score 更新）
  - stats 统计
  - path_filter 过滤（mesh 资产不被 3dgs 检索命中）
- **未集成**：场景资产检索/入库（M8 场景锚完整集成时接入），asset_management skill（暂不需要）

### M7 完成详情（2026-09-12）
- **目标**：v3 mesh 路径端到端联调 — `auto` 模式 `--character-mode mesh` 全链路跑通
- **启动服务**：
  - FLUX ComfyUI (GPU0:8192) — 之前已停，本次重启（clip_l 符号链接到 HunyuanVideo 目录）
  - TTS server (GPU2:9880) — cosyvoice 环境，MODELSCOPE_OFFLINE=1 避免网络超时
  - 已有：H3(8188) / Wan+RIFE(8189) / HunyuanVideo(8190) / Hunyuan3Dv2(8193)
- **测试命令**：
  ```bash
  python core/vidance.py auto "雪原小屋的守望者" \
    --character "穿红斗篷的短发少年" --character-mode mesh --voice edge-moe
  ```
- **全链路流程**：
  1. **编剧**：LLM 生成 3 镜脚本（雪原守望），cinematic 冷蓝调风格
  2. **prompt 优化**：角色 prompt + 3 镜 video/bg prompt
  3. **角色锚 (mesh)**：
     - FLUX → character_ref.png (1024×1024, ~20s)
     - Hunyuan3Dv2 → character.glb (8.7MB, ~30s warm)
     - mesh_render 4 角度预览
     - 审查超时降级 score=5（API 不稳定，非 mesh 质量问题）
  4. **镜头生成**（3 镜并行预取 + 串行 I2V）：
     - Shot 1 (yaw=0°): Wan I2V 120帧 → review 7 (pass) → RIFE 2x → 233帧
     - Shot 2 (yaw=90°): Wan I2V 96帧 → review 7 (pass)
     - Shot 3 (yaw=180°): attempt 1 review 2 (角色缺失+白块遮挡) → retry attempt 2 review 7 (pass) → RIFE 2x → 233帧
  5. **镜头间过渡**: 1→2 RIFE 光流 (9帧), 2→3 crossfade
  6. **合成**: final.mp4 (832×480, 10.1s, h264/yuv420p)
  7. **调色**: cinematic LUT
  8. **配乐**: calm BGM (11.8s, numpy 合成)
- **成片**: `output/20260912_134303/final.mp4` (1.2MB, 10.1s)
- **资产入库**: 未入库（角色审查超时 score=5 < 7，阈值设计正确）
- **可视化**:
  - `m7_overview.png` — 全步骤拼图（FLUX ref + 4 角度预览 + 3 合成参考帧 + 视频首帧）
  - `m7_results.html` — 浏览器可视化页面（含内嵌视频播放）
- **关键发现**：
  - v3 mesh 路径全链路跑通：FLUX→Hunyuan3Dv2→mesh_render→composite→Wan I2V→RIFE→LUT→BGM→合成
  - 审查 API 超时仍是主要问题（4/5 次审查超时降级），但不阻塞流水线
  - Shot 3 attempt 1 审查返回 score=2（角色缺失+白块遮挡），retry 后 pass — 审查机制有效
  - 资产入库阈值 score≥7 工作正常（超时降级 score=5 未入库）
  - FLUX 重启需 clip_l.safetensors（从 HunyuanVideo 目录符号链接）
