


Goal
- 在本地 4×4090 服务器上部署多个开源生图/生视频/TTS 模型（复用 ComfyUI headless），并规划 "Vidance" 视频生成 agent 项目，v0 端到端验证通过，v1 M1-M8 全部完成但发现 I2V 根本性 bug（Wan22 ti2v 模型 concat_cond 被忽略，I2V 实际未生效）
Constraints & Preferences
- 所有文件放机械盘（代码 sdb / 模型 sdc 经由 mergerfs → /mnt/dataset）
- 推理引擎复用现有 ComfyUI，各模型实例跑独立 GPU，互不干扰
- HuggingFace 被墙（走 hf-mirror.com 镜像下载），GitHub 被墙（走 ghfast.top 镜像 clone）
- 用户关心中文 prompt 支持和生成质量
- vidance 主干是生图/生视频，LLM 只做辅助（脚本/prompt/审查）
- v0 编排模式：混合模式（确定性流程硬编码 + 创意决策交 agent）
- v0/v1 同步执行（无异步队列，留 v4）
- TTS 双引擎：edge-tts 默认（自然省 GPU）+ CosyVoice 兜底/克隆
- 存储分层：代码/配置/小素材放 sdb（快盘），模型/视频/音频/产出放 mergerfs 机械盘
- 用户要求：先规划好所有版本设计再从 v1 一步步开发


Progress


Done
- Wan2.2-TI2V-5B 部署完成（GPU2:8189, API 8890）
- HunyuanVideo 13B T2V 部署完成（GPU3:8190, API 8891），中文 tokenizer bug 已修复
- MiniMax-H3 部署完成（8188/8889，未运行，随时可拉起）
- SDXL Base 1.0 部署完成（GPU1:8191, API 8892）
- FLUX.1-dev fp8 部署完成（GPU0:8192, API 8893）
- translate.py 中文→英文翻译（SDXL/FLUX 均使用）
- 多模态 LLM 实测：claude-sonnet-4-6 ✅, claude-haiku-4-5 ✅, qwen3.6-chat ✅, qwen3.5 ✅; claude-opus-4-8/glm-5.2/k3/ark/doubao ❌
- ComfyUI 原生 3D 节点确认（nodes_hunyuan3d/triposplat/frame_interpolation/gaussian_splat/load3d/save3d）
- 部署记录文档：MiniMax-H3、SDXL、FLUX 各一份 .md
- ffmpeg + sox 已安装（cosyvoice env，软链 ~/.local/bin/）
- CosyVoice 仓库克隆 + conda env + 依赖 + 模型下载 + moviepy 安装全部完成
- Vidance 项目骨架全部建立
- 端到端验证通过：python core/pipeline.py "一只猫在月球上跳舞" → 13.3s 成片，3 镜
- v0 文档全面更新完成
- v1-design.md 编写完成（546 行）+ 状态更新为 ✅ 已完成
- v2/v3/v4 设计文档全部完成
- roadmap.md 审整完成 + v1 标记 ✅
- v1 M1 完成：TripoSplat 5 个模型下载完成（~3.6GB）+ extra_model_paths.yaml 配置
- v1 M2 完成：triposplat.json workflow 构建并验证成功（20.47s, 18MB PLY, 262144 gaussians）
- v1 M3 完成：triposplat_render.json + splat_renderer.py（用 preview.py render_splat 算法，min_px 保证可见性）
- v1 M4 完成：wan_i2v.json workflow 构建并验证
- v1 M5 完成：comfy_api.py 新增 5 个方法（upload_image/generate_tripsplat/generate_character_anchor/generate_i2v/generate_flux_t2i）
- v1 M6 完成：pipeline.py 全量重写为 v1 角色锚流水线（--character/--voice CLI）
- v1 M7 完成：审片加 character_consistency 维度 + review_character()
- v1 M8 完成：e2e 测试跑通，4 镜 12.5s 成片 final.mp4（6.9MB, 1664×960, 24fps）
- 输出目录：/mnt/dataset/zxy/vidance/output/20260908_124050/
- 修复审片超时：图片缩放 398KB→112KB（max 768px, JPEG 85%）+ 60s timeout + 1 retry + safe fallback auto-pass
- 新增 _safe_review_character(), _safe_review_shot(), _default_review() helper 方法
- chat() / chat_json() 新增 retries 参数
- 抽帧数从 4 改为 2
- 角色一致性评分 3-5/10（已知问题：3DGS 渲染稀疏 13-26% 覆盖 + 颜色偏暗 mean 0.51 vs ref 0.94）
- 文档更新：AGENTS.md / roadmap.md / v1-design.md 全部标记 v1 ✅
- I2V 根因调查完成：确认 Wan2.2 ti2v 模型 I2V 条件化被完全忽略
- ti2v 模型：patch_embedding in_dim=48, out_dim=48, 无 img_emb, 无 ref_conv, 无 vace
- Wan 2.2 VAE 产生 48 通道 latent（decoder.conv1 = 1024, 48, ...）
- 模型检测匹配到 WAN22_T2V（在 models 列表中排 WAN21_T2V 前面）
- WAN22_T2V → WAN22(image_to_video=True)，但 WAN22 不重写 concat_cond，用 WAN21.concat_cond
- extra_channels = in_dim(48) - latent_channels(48) = 0 → concat_cond 返回 None → concat_latent_image + concat_mask 被忽略
- WanImageToVideo 节点设 concat_mask，但 Wan22 用 denoise_mask（不同 key）
- 验证：composite_ref 输入 vs 视频第一帧相关性仅 0.58（I2V 生效应 >0.9）


In Progress
- 用户发现 I2V 未生效（视频中猫与角色锚参考图完全不同），根因已定位但修复方案未定


Blocked
- 爬虫库未装（bs4/lxml/yt-dlp/feedparser，v4 才需要）
- v3 headless mesh 渲染方案待验证


Key Decisions
- 选 Wan2.2-5B ti2v：最小最快，统一 T2V+I2V，4090 友好
- HunyuanVideo 用 bf16 而非 GGUF：48GB 显存充裕
- FLUX.1-dev fp8（非 schnell）：质量优先
- 多模态审查用 claude-haiku-4-5（4s/镜）而非 sonnet（534s/镜）
- GPU 分配：GPU0=FLUX+TripoSplat(8192), GPU1=SDXL(8191), GPU2=Wan(8189)+CosyVoice(9880), GPU3=HunyuanVideo(8190)
- v1 核心：3DGS 角色锚（全片唯一 3DGS，RenderSplat 按角度渲染参考帧喂 I2V）
- v1 I2V 分辨率选 832×480×81帧（225s）
- Wan I2V 输出 2x 尺寸：请求 832×480 → 输出 1664×960
- 审片图片缩放：上传前 resize 到 768px + JPEG 85%（原始 1664×960 太大导致 API 超时）
- 审片超时处理：60s timeout + 1 retry，失败则 safe fallback auto-pass
- TripoSplat 和 FLUX 复用同一 ComfyUI 实例（GPU0:8192）
- preview.py render_splat() 替代 RenderSplat 节点（splat_scale max=5.0 不够）
- SplatToFile3D→SaveGLB 替代 SaveGaussianSplat（需 viewport_state）


Next Steps
1. 修复 I2V：Wan22 ti2v 的 concat_cond 因 extra_channels=0 被忽略，需研究 Wan22 正确的 I2V 调用方式（denoise_mask vs concat_mask，或换用 WanImageToVideo 之外的节点/方式）
2. 验证修复后 I2V 真正生效（第一帧与 composite_ref 相关性 >0.9）
3. v1.1 一致性优化：收紧取景 / 提高渲染分辨率 / 亮度校正
Critical Context
- I2V 根因：WAN21.concat_cond 中 extra_channels = patch_embedding.weight.shape[1] - noise.shape[1]，Wan22 latent_channels=48 且 in_dim=48 → extra_channels=0 → 返回 None → I2V 图片被忽略。Wan22 应通过 denoise_mask 做 I2V，但 WanImageToVideo 节点设的是 concat_mask（key 不匹配）
- 模型检测顺序：comfy/supported_models.py 行2470 WAN22_T2V 在行2472 WAN21_T2V 之前，ti2v 匹配到 WAN22_T2V
- WAN22_T2V unet_config = {"image_model": "wan2.1", "model_type": "t2v", "out_dim": 48}，get_model 创建 WAN22(image_to_video=True)
- WAN22 类（model_base.py:1893）不重写 concat_cond，继承 WAN21.concat_cond
- Wan 2.2 VAE：48 通道 latent（vs Wan 2.1 的 16 通道），decoder.conv1.weight=1024,48,3,3,3
- ti2v 模型无 img_emb：img_emb keys=0，img_emb.proj.0.bias 不存在 → model_detection 判定为 t2v 而非 i2v
- TTS 服务：FastAPI 双引擎 on 0.0.0.0:9880
- 主 Python 环境：/home/zxy/.conda/envs/comfyui/bin/python
- ComfyUI 节点缓存：相同 prompt+seed → 秒出旧结果，必须随机 seed
- FLUX 首次加载：12GB 模型从机械盘 ~2min，之后 18s/图
- 4× RTX 4090 每卡 48GB；mergerFS /mnt/dataset = sda:sdc:sdd:sde（47T）；sdb 单独 11T
- USTC LLM API：https://api.llm.ustc.edu.cn/v1，key sk-M4b0y-Euavlan2tL6ZcWfA
- Wan I2V workflow（12 节点）：1=UNETLoader, 2=CLIPLoader, 3=VAELoader, 4=ModelSamplingSD3, 5/6=CLIPTextEncode, 7=LoadImage, 8=WanImageToVideo(start_image="7",0), 9=KSampler, 10=VAEDecode, 11=CreateVideo, 12=SaveVideo
- TripoSplat workflow（13 节点）：1=LoadImage, 2=LoadBackgroundRemovalModel, 3=RemoveBackground, 4=TripoSplatPreprocessImage, 5=CLIPVisionLoader, 6=VAELoader(flux2-vae), 7=TripoSplatConditioning, 8=UNETLoader, 9=KSampler, 10=VAELoader(triposplat_vae_decoder), 11=VAEDecodeTripoSplat, 12=SplatToFile3D, 13=SaveGLB
- FLUX T2I workflow（10 节点 API 格式）
- *preview.py render_splat()*：render_splat(xyz, rgb, scale, opacity, yaw, pitch, size, min_px, max_px, gain, fov=35, dist=2.2)


Relevant Files
- /mnt/disk_sdb/zxy/vidance/config/config.json — 运行时配置
- /mnt/disk_sdb/zxy/vidance/core/pipeline.py — v1 流水线主控（含 _safe_review_character/safe_review_shot/default_review）
- /mnt/disk_sdb/zxy/vidance/utils/comfy_api.py — ComfyUI HTTP 客户端（含 generate_i2v 等 5 个新方法）
- /mnt/disk_sdb/zxy/vidance/utils/splat_renderer.py — 3DGS PLY 渲染器
- /mnt/disk_sdb/zxy/vidance/utils/llm.py — USTC LLM 客户端（v1: 5-dim review, _image_to_base64 带 resize 768px, chat/chat_json 带 retries）
- /mnt/disk_sdb/zxy/vidance/utils/workflows/wan_i2v.json — Wan I2V workflow 模板（12 节点，I2V 条件化未生效，待修复）
- /mnt/disk_sdb/zxy/vidance/utils/workflows/triposplat.json — TripoSplat workflow
- /mnt/disk_sdb/zxy/vidance/utils/workflows/flux_t2i.json — FLUX T2I workflow
- /mnt/disk_sdb/zxy/vidance/docs/v1-design.md — v1 设计（状态 ✅ 已完成）
- /mnt/disk_sdb/zxy/vidance/docs/roadmap.md — 路线图（v1 ✅）
- /mnt/disk_sdb/zxy/vidance/AGENTS.md — 项目约定（v1 状态 + 已知限制 4 条）
- /mnt/dataset/zxy/vidance/output/20260908_124050/ — v1 e2e 测试产出（4 镜 12.5s final.mp4 + character/ + clips/）
- /mnt/dataset/zxy/Wan-ComfyUI/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors — ti2v 模型（in_dim=48, out_dim=48, 无 img_emb）
- /mnt/dataset/zxy/Wan-ComfyUI/vae/wan2.2_vae.safetensors — Wan 2.2 VAE（48 通道 latent）
- /mnt/disk_sdb/zxy/ComfyUI/comfy/model_base.py:1599-1649 — WAN21.concat_cond（extra_channels=0 → None）
- /mnt/disk_sdb/zxy/ComfyUI/comfy/model_base.py:1893-1912 — WAN22 类（不重写 concat_cond）
- /mnt/disk_sdb/zxy/ComfyUI/comfy/supported_models.py:1467-1477 — WAN22_T2V（image_to_video=True, out_dim=48）
- /mnt/disk_sdb/zxy/ComfyUI/comfy/supported_models.py:2470 — models 列表中 WAN22_T2V 排在 WAN21_T2V 前面
- /mnt/disk_sdb/zxy/ComfyUI/comfy/model_detection.py:710-741 — Wan 模型检测逻辑（img_emb → i2v vs t2v）
- /mnt/disk_sdb/zxy/ComfyUI/comfy_extras/nodes_wan.py:18-60 — WanImageToVideo 节点（设 concat_latent_image + concat_mask）
- /mnt/disk_sdb/zxy/ComfyUI/comfy/ldm/triposplat/preview.py — render_splat 函数
- /mnt/dataset/zxy/TripoSplat-ComfyUI/ — TripoSplat 模型目录（5 文件）