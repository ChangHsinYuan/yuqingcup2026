# Vidance v3 设计文档 — 2D→3D→新视角

> 总览与 v0-v4 路线见 [roadmap.md](./roadmap.md)，v1/v2 见 [v1-design.md](./v1-design.md)/[v2-design.md](./v2-design.md)
>
> **状态：📐 设计中**（未开始实现，v2 已完成可启动，⚠️ headless 渲染阻塞待解）
>
> **⚠️ 已知阻塞**：mesh 渲染到新视角图像无纯 Python headless 节点，需先解决渲染方案（见 §10）。

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
| headless mesh 渲染新视角（待定方案） | 物理仿真 |
| mesh → GLB 导出（SaveGLB） | 4D 重建（视频→动态3D） |
| mesh 资产库管理 | 爬热点素材（v4） |
| mesh 渲染参考帧 → Wan I2V | 异步任务制（v4） |
| 3DGS ↔ mesh 双路径（角色可选） | |

### 1.5 核心验证点

1. Hunyuan3Dv2 单图→mesh 质量（几何完整性、纹理）
2. 多视角条件（4 图）是否显著优于单图重建
3. **headless mesh 渲染方案能否跑通**（最大阻塞，见 §10）
4. mesh 渲染新视角 → Wan I2V，角色/场景一致性是否优于纯 3DGS
5. GLB 资产导出与跨任务复用可行性
6. 3DGS（v1）与 mesh（v3）双路径适用场景区分

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
│           渲染: headless mesh renderer (待定) + RenderSplat│
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

### 3.3 headless mesh 渲染（⚠️ 阻塞点）

这是 v3 最大技术风险。ComfyUI 现状：
- `SaveGLB`：纯 Python 写 GLB 文件 ✅ headless OK
- `Save3DAdvanced` / `SaveGaussianSplat`：需 `viewport_state`（来自 Load3D 浏览器节点）❌ 无法 API 调用
- **无纯 Python "mesh + camera → IMAGE" 节点**

可选方案（见 §10 详述）：
1. **trimesh + pyrender**（推荐）：纯 Python，OpenGL 离屏渲染，headless
2. **Blender headless**：`blender -b --python render.py`，质量高但重
3. **Isaac Sim**：NVIDIA 仿真级，质量最高但部署重
4. **自写 ComfyUI 节点**：封装 trimesh 渲染为节点（最贴合现有架构）

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

### 5.1 Hunyuan3Dv2 重建（ComfyUI，新 workflow）

单图路径（`workflows/hunyuan3d_single.json`）：
```
LoadImage → CLIPVisionEncode → Hunyuan3Dv2Conditioning → KSampler
EmptyLatentHunyuan3Dv2(resolution=3072) ──────────────→  ↓
UNETLoader(hunyuan3dv2) + ModelSamplingAuraFlow ─────→  ↓
VAEDecodeHunyuan3D(octree_resolution=256) → VoxelToMesh("surface net") → SaveGLB
```

多视角路径（`workflows/hunyuan3d_multiview.json`）：
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

模型文件（需下载）：
| 文件 | 目录 | 说明 |
|------|------|------|
| hunyuan3d-v2 模型 | diffusion_models/ | ~12G |
| hunyuan3d-v2 VAE | vae/ | ~1G |
| (CLIP vision 复用 v1 的) | clip_vision/ | — |

### 5.2 headless mesh 渲染（待定，见 §10）

接口设计（无论选哪个方案）：
```python
# utils/mesh_render.py
def render_mesh_view(mesh_path: str, camera: dict, output_path: str,
                     width=1280, height=704, bg_image=None) -> str:
    """渲染 mesh 指定视角 → IMAGE，可选合成背景"""
    # camera: {yaw, pitch, fov, position, look_at}
```

### 5.3 复用 v1/v2 组件

3DGS 路径（TripoSplat/RenderSplat）、Wan I2V、TTS、RIFE/ffmpeg 后期 —— 全部复用。

---

## 6. GPU 与显存规划

### 6.1 v3 GPU 分配

| GPU | 服务 | 显存 | v3 变化 |
|-----|------|------|---------|
| GPU0 | FLUX (8192) + TripoSplat | 36G + ~18G | 复用，多视角图生成 |
| GPU1 | Hunyuan3Dv2 (8191) | ~15-20G | **v3 新增**：3D 重建 |
| GPU2 | Wan I2V (8189) + TTS (9880) | 17.8G + 2.5G | 复用 |
| GPU3 | HunyuanVideo + RIFE + mesh渲染 | 20G + 3G + ~2G | mesh 渲染（trimesh CPU 或 GPU） |

- Hunyuan3Dv2 重建放 GPU1（SDXL 让位或共享，SDXL 在 v3 角色非主力）
- mesh 渲染：trimesh/pyrender 用 CPU 或 GPU3 空闲
- 多视角重建显存：4 图 CLIP encode + 3D latent，估 ~15-20G

---

## 7. Workflow 模板清单（v3 新增）

| 模板 | 用途 |
|------|------|
| `workflows/hunyuan3d_single.json` | 单图→mesh |
| `workflows/hunyuan3d_multiview.json` | 多视角→mesh |
| `workflows/mesh_render.json` | mesh→视角图（若自写节点方案） |

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

| 模型 | 用途 | 来源 | 状态 |
|------|------|------|------|
| Hunyuan3Dv2 | 图→mesh 重建 | hf-mirror | ❌ 待下载 |

### 9.2 headless 渲染方案（阻塞，见 §10）

| 方案 | 依赖 | 状态 |
|------|------|------|
| trimesh + pyrender | pip | ❌ 待验证 |
| Blender headless | Blender 安装 | ❌ 待验证 |
| 自写 ComfyUI 节点 | 封装 trimesh | ❌ 待定 |

### 9.3 代码新增

| 文件 | 改动 |
|------|------|
| `core/pipeline.py` | 角色双路径 + 场景锚阶段 |
| `utils/mesh_render.py` | headless mesh 渲染 |
| `utils/hunyuan3d.py` | Hunyuan3Dv2 重建客户端 |
| `utils/workflows/hunyuan3d_*.json` | 新 workflow |
| `utils/asset_registry.py` | 资产库管理 |
| `.opencode/agents/asset.md` | asset subagent |
| `.opencode/skills/asset_management/SKILL.md` | 资产管理规范 |

---

## 10. ⚠️ headless 渲染方案（关键阻塞）

这是 v3 能否启动的**前置条件**。当前 ComfyUI 无法 headless 渲染 mesh 到图像。

### 10.1 方案对比

| 方案 | 质量 | 部署复杂度 | headless | 纹理/灯光 | 建议 |
|------|------|-----------|----------|----------|------|
| **trimesh + pyrender** | 中 | 低（pip） | ✅（OSMesa/EGL） | 基础 | **首选验证** |
| **Blender headless** | 高 | 中（装 Blender） | ✅（`-b`） | 完整 | 质量不够再上 |
| **Isaac Sim** | 最高 | 高（NVIDIA 仿真栈） | ✅ | 完整 | 过重，最后考虑 |
| **自写 ComfyUI 节点** | 取决于内核 | 中 | ✅ | 取决于内核 | 贴合架构，中期 |

### 10.2 trimesh + pyrender 路线（首选）

```bash
pip install trimesh pyrender pyglet
# headless 需离屏渲染后端
sudo apt install libosmesa6-dev  # OSMesa
# 或用 EGL（NVIDIA GPU）
```

```python
# utils/mesh_render.py 伪代码
import trimesh, pyrender
def render_view(glb_path, camera, out_path, w, h):
    mesh = trimesh.load(glb_path)
    pr = pyrender.OffscreenRenderer(w, h)
    scene = pyrender.Scene()
    scene.add(pyrender.Mesh.from_trimesh(mesh))
    cam = pyrender.PerspectiveCamera(yfov=radians(camera['fov']))
    scene.add(cam, pose=compute_pose(camera))
    color, _ = pr.render(scene)
    Image.fromarray(color).save(out_path)
```

**风险**：
- pyrender 离屏渲染在无显示器的服务器上需 OSMesa/EGL 配置
- 纹理/材质支持有限（GLB 的 PBR 材质可能丢失）
- 无复杂灯光（需手动加光）

### 10.3 验证里程碑（v3 启动前必须完成）

```
[阻塞验证] trimesh+pyrender 能否在 4×4090 服务器 headless 渲染 GLB → IMAGE？
  ├─ 成功 → v3 用此方案
  └─ 失败 → 试 Blender headless → 再失败试 Isaac Sim
```

**结论先行**：此验证不通，v3 无法启动。建议在 v1/v2 实现期间穿插验证。

---

## 11. 验收标准

v3 跑通的标志：

1. **headless 渲染**：GLB → 任意视角 IMAGE，headless 可 API 调用（§10 阻塞解决）
2. **角色 mesh**：Hunyuan3Dv2 多视角重建 → GLB，多角度预览审查通过
3. **场景 mesh**：场景图 → GLB → 多镜头视角渲染，场景跨镜头一致
4. **双路径**：角色可选 3DGS 或 mesh，agent 按质量需求决策
5. **资产库**：character/scene mesh 入库，跨任务可检索复用
6. **视角一致性**：同场景多镜头，场景结构一致（优于 v2 的独立 2D 背景）
7. **端到端**：概念 → 含场景一致性的长片，1-3 分钟

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| headless 渲染方案跑不通 | v3 阻塞 | §10 三方案递进；最差降级用 3DGS 场景（RenderSplat 扩展） |
| Hunyuan3Dv2 重建质量不足 | mesh 不可用 | 多视角条件提升；备选保留 3DGS 路径 |
| GLB 纹理/材质在 pyrender 丢失 | 渲染图丢色 | trimesh 顶点色 fallback；或 Blender |
| 多视角图视角不一致（FLUX 各生成各） | 重建扭曲 | FLUX prompt 强约束视角 + 用参考图控制 |
| mesh 渲染慢 | 流水线慢 | mesh 预渲染缓存；资产复用减少重复重建 |
| GPU1 显存（Hunyuan3D + SDXL） | OOM | SDXL 让位；Hunyuan3D 用完释放 |

---

## 13. 实现里程碑

| 阶段 | 内容 | 产出 |
|------|------|------|
| M0 | **headless 渲染验证**（§10 阻塞解锁） | trimesh 渲染 GLB 成功 |
| M1 | Hunyuan3Dv2 模型下载 + single workflow 验证 | 角色 mesh |
| M2 | multiview workflow + 多视角重建质量对比 | 多视角 mesh |
| M3 | mesh_render 封装 + 视角渲染验证 | 新视角 IMAGE |
| M4 | 场景锚阶段（场景重建 + 渲染背景） | 场景一致性 |
| M5 | 角色双路径（3DGS/mesh）选择 | 双路径打通 |
| M6 | 资产库 + asset subagent | 资产复用 |
| M7 | pipeline 集成 + 端到端联调 | 闭环 |
| M8 | 场景一致性长片验证 | v3 成片 |

> **M0 是 v3 启动门槛**，建议在 v1/v2 期间穿插完成。
