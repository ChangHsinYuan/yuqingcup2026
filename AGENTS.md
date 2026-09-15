# Vidance — AI 有声短片生成

> 本文件供 opencode agent 读取，了解项目约定与命令。

## 项目概述

Vidance 是一个基于 opencode agent 编排的本地视频生成系统。核心流程：中文概念→LLM编剧→Wan T2V生成→双引擎TTS配音→多模态审片→ffmpeg合成→有声短片。

**统一入口**：`python core/vidance.py {auto|custom|quick}` — auto 模式走 LLM 编剧+Wan/FLUX 生成，custom 模式走参考图+预写脚本+H3 ref2va 生成，quick 模式纯 T2V 无后处理。后处理（RIFE/LUT/BGM/STT）由 `core/postprocess.py` 共享模块提供。

**当前状态：v2 长视频完成 + v2.1 backlog 接线完成 + v3 完成（M0-M7，M8 跳过）+ v4 M1-M6 完成 + v5 剪辑特效完成（M1-M4）+ v6 prompt 多轮核实完成（M1-M4）+ v7 前端 M1-M4 代码完成（端到端待 GPU）+ v8/v9 文档已就绪**（2026-09-15）。

**版本路线**：v4 营销号流水线（fast/hot 爬图出片）→ v5 剪辑特效 → v6 prompt 多轮核实（ask 交互 + dialog API）→ v7 前端 agent WebUI（仿豆包，左栏+画布+发框+`/`命令调 tool，访问 :8894/ui）→ v8 一镜到底 → v9 机器人网关（企微/飞书/钉钉…）。

v1 角色锚流程：FLUX 生角色图 → TripoSplat 重建 3DGS → 每镜 RenderSplat 按角度渲染参考帧 → Wan I2V 生成。

**v2 长视频**（M1-M5 全部完成）：
- **M1 RIFE 光流插帧**：镜头间光流过渡（rife_v4.26，multiplier=8，0.375s@24fps）
- **M2 镜头并行预取**：ThreadPool 并行预取 FLUX 参考帧 + TTS 配音（与 Wan I2V 串行不冲突）
- **M3 ffmpeg 调色**：6 种 3D LUT（cinematic/warm/cool/vintage/vivid/soft），纯 numpy 生成，LLM 自动选风格
- **M4 配乐 ducking**：5 种 BGM（calm/uplifting/mysterious/dramatic/playful），纯 numpy 合成，sidechaincompress ducking
- **M5 faster-whisper STT**：large-v3-turbo 模型，成片音频转写→SRT→重新烧录字幕

**v2.1 backlog**（全部完成）：
- `--duration N` 动态镜头数 + `--slowmo N` 全局慢动作 + per-shot `transition_out` + `concat` 子命令

**v3 2D→3D→新视角**（M0+M1+M2+M3+M4+M5+M6+M7 完成，M8 待开始）：
- **M0 headless 渲染** ✅：trimesh 5.1.0 + pyrender 0.1.45 + EGL 后端，4×4090 headless 渲染 GLB→IMAGE 成功
- **M1 Hunyuan3Dv2 mesh 重建** ✅：DiT turbo（4步,~61s）+ VAE + DINOv2-giant，doubao.jpg→257K verts GLB，pyrender 4 角度预览通过
- **M2 multiview** ✅：3 视角（front/left/back）裁剪自 doubao.jpg 三视图 → 206K verts GLB（~22s warm），aspect 1.69 vs 单图 1.02，主连通分量 63% vs 38%，多视角显著优于单图
- **M3 mesh_render 封装** ✅：`utils/mesh_render.py`（260行），3 层 API + 3-point lighting + depth-based alpha + 背景合成，与 splat_renderer API 对齐，8 项测试全通过（832×480 渲染 0.79s/张）
- **M4 场景锚** ✅：FLUX→Hunyuan3Dv2→mesh_render 全链路验证，2 场景（隔离木屋 238K verts + 雪原环境 411K verts），8 角度渲染一致性确认
- **M5 角色双路径** ✅：`--character-mode mesh` 接入 pipeline，`utils/hunyuan3d.py` 客户端（单图+多视角→GLB），`_build_character_anchor` + `_generate_scene_ref` mesh 分支，doubao.jpg→282K verts GLB 28.8s + 4 角度预览 + 合成参考帧验证通过
- **M6 资产库** ✅：`utils/asset_registry.py`（CRUD+模糊搜索+CLI），pipeline 集成（角色 mesh/3dgs 重建前查库复用 + 重建后自动入库），`--no-asset-reuse` flag，`.opencode/agents/asset.md` subagent，14 项测试全通过
- **M7 端到端联调** ✅：`auto --character-mode mesh` 全链路跑通（FLUX 三视图→Hunyuan3Dv2 multiview→mesh_render→Wan I2V→RIFE slowmo→LUT→BGM→合成），3 镜 12.8s 成片 `output/20260912_183520/final.mp4`。**修复**：RIFE slowmo（4帧→233帧，concat demuxer→image2 demuxer）、LLM 审查模型（claude-haiku-4-5 下架→qwen3.8-chat）、审查超时（60s→180s+retries=2）、multiview mesh（单图 Z=0.009 纸片→三视图 Z=1.56 真实3D）。审查系统生效：角色 score=3、Shot 2 score=3.2/4（retry）、Shot 3 score=6/5/5（retry）

**v4 营销号流水线**（M1-M6 完成；~~批量联调/无人值守~~ 已确认跳过；发布不做，人精选手动上传）：
- **M1 爬虫+选题** ✅：`utils/crawler.py`（B站 API+微博+知乎+百度+RSS+yt-dlp 多源热点抓取）+ `llm.scout_topics()`（热点→概念候选排序）+ `.opencode/agents/scout.md` subagent + `.opencode/skills/topic_scouting/SKILL.md`。端到端验证：45 条热点（B站/微博/知乎各 15）→ 5 个概念候选（9/8/8/7/7 分），概念有画面感且结合热点创意角度
- **M2 异步任务队列** ✅：`core/scheduler.py`（`TaskQueue` SQLite 持久化 + `Scheduler` 线程轮询 + CLI）+ `core/api_server.py`（FastAPI :8894，POST/GET/DELETE /api/tasks + /api/health + /api/scout + /api/dashboard）+ `core/dashboard.py`（自包含 HTML 仪表盘，base64 缩略图，30s auto-refresh）。`pipeline.py` + `vidance.py` 加 `--task-id` 参数。端到端验证：3 任务提交 → 串行执行（max_concurrent=1）→ 3 成片（柴犬 12.7s / 赛博朋克 11.4s / 小厨娘 11.9s），可视化产出 `output/dashboard.html` + `output/m2_results.html`
- **M3 声音克隆生产化** ✅：`utils/voice_clone.py`（B站搜索 API 随机 buvid3 过反爬 → yt-dlp 下载按 bvid 隔离 → faster-whisper VAD 切段（跳过纯 ♪ 器乐、长块取 8s 窗口、均分质量门 -32dB）→ 24kHz mono 16bit ffmpeg 转换 → STT 转写 prompt_text → 入库 voices/{name}/ 自动注册 cosy-{name} → 测试合成存 voice_samples/）。TTS server 加 `POST /voices/reload` 热加载（免重启注册新音色）。API server 加 `POST /api/clone_voice` 异步克隆端点 + `GET /api/voices`。端到端验证：3 音色克隆全通（xinwen1 新闻播音 / jieshuo1 影视解说 / jilupian1 纪录片，单音色 21-30s），STT 转写回验内容一致，注册音色可按名直接调用
- **M4 FunClip 智能裁剪** ✅：`utils/funclip.py`（FunASR `speech_paraformer-large-vad-punc` 转写 + 字级时间戳 + 字级聚合成句 + ffmpeg 时段裁剪拼接 + LLM 语义 keep/drop）。三子命令 CLI（transcribe/clip/smart）。**GPU 修复**：满卡（GPU0 被 H3 占满）连 CUDA context 都建不出来（`cudaMemGetInfo` 直接 OOM），`device='auto'` 逐卡探测跳过建不了 context 的卡并选空闲显存最多的卡。端到端验证：4 句水母剧本拼 12.6s 长音频，指令"只要讲光的句子" → LLM 保留 2 句（含语义含"点亮之光"的一句）drop 2 句 → 输出 3.88s，转写回验内容一致；CLI OOM 修复后 14 字全对
- **M5 素材爬取** ✅：`utils/asset_crawler.py`（必应图片 async 接口搜图下载 + magic bytes 校验 + md5 去重 + LLM concept→关键词 + incompetech BGM 按 feel 搜曲下载 loudnorm→bgm/{mood}.wav）+ 四子命令 CLI（keywords/images/refs/bgm）。**修正**：pieces.json 的时长字段是 `length`（HH:MM:SS 格式）非 duration；filename 自带 `.mp3` 后缀不可重复拼（拼重会 404）；百度图片 acjson 接口需真 cookie 已弃用，必应 `cn.bing.com/images/async` 直连可达。端到端验证：concept"雪山上的日出小狐狸"→LLM 3 关键词→6 张参考图（19-129KB）；`bgm epic --list` 5 首候选（58-62s）→ 爬取 epic.wav（61.4s 10.8MB loudnorm）；pieces.json 固化到 bgm/pieces.json
- **M6 快速营销号链路** ✅：`utils/fastline.py`（`FastLine`）+ `vidance.py fast/hot` 子命令——**跳过视频模型**：LLM 规划 N 段旁白+每段运镜（pan/zoom-in/zoom-out）→ 图片（`--images-source crawl` 必应爬图+**多模态相关性校验**自动删不相关图、不足 FLUX 补足；或 `flux` 逐段文生图；或 `--images` 自供）→ ffmpeg zoompan(Ken Burns) → 逐段 TTS → crossfade 拼接 → LUT/BGM/STT 复用 PostProcessor。**热搜来源**：`--source-topic`+`--trend-date` 写进 meta + 片头顶部 drawtext 角标（独立于字幕，不造成错位）。`--slowmo N` 另出 `final_slow.mp4`（RIFE，视频-only），原片 `final.mp4` 恒保留。**`hot` 子命令**：爬实时热点→LLM scout 自动选题→fast 一步到位。**FLUX 生图质量门**：`optimize_fastline_prompt()` 中文旁白→英文 FLUX prompt（修"塞中文生成无关图"），生成后 `assess_image_relevance` 审核贴合旁白、不过重生成≤2（实测背包青蛙 score=10 / 程序员删89TB 3图 9/7/9）。端到端验证：scout 从 40 条真实热点选"旅行青蛙停运"9 分，crawl 校验滤掉 3 张不相关图（医院/充电器/回收标志均 score=1.0）FLUX 补足；热搜角标+字幕 0s 对齐。**修正**：①`--bgm` choices 加 `epic`；②`_image_to_base64` 支持 RGBA/P/LA/PA 转 RGB（修 `cannot write mode P as JPEG`）；③RIFE slowmo 不带音轨（helper 仅重编码画面）；④**服务缺省兜底（2026-09-15）**：TTS 9880 不可用 → 静音音频兜底 + 打印 `⚠ [TTS 缺省]`（含启动提示）；爬图挂 → 转 FLUX，FLUX 挂 → 纯色渐变占位图（`_gen_placeholder_image`，带概念水印）+ `⚠ [图源/FLUX 缺省]`；plan_narration/select_lut/select_bgm 原有兜底不变——**GPU 服务全关时 fast 任务降级跑完出片不崩**（实测：Pexels 照常 + 静音 + 占位图 → 6.0s completed）

**v5 剪辑特效**（M1-M4 完成，2026-09-14）：
- **M1 特效库** ✅：`utils/effects.py`（8 种镜头内特效：flash/punch_in/glitch/grain_vignette/speed_ramp/freeze_zoom/wipe/zoom，纯 ffmpeg yuv420p）+ 单片段 CLI
- **M2 xfade 转场** ✅：`transition()` 双片段 xfade + `concat_with_transitions()` 单 filter_complex 链式 N 片段拼接（fade/slide/wipe/pixelize/circleopen 等 30+ 种）
- **M3 LLM 自动选** ✅：`llm.select_effects()` 按每镜内容/情绪选特效 + 镜头间转场（`fast/hot --effects auto`，pipeline 内 `apply_effects_to_clips` 施加，失败回退原片）
- **M4 端到端** ✅：`fast "夜色中城市霓虹与火光" --effects auto` 3 镜 → LLM 选 glitch/grain_vignette/freeze_zoom → 9.0s 成片；meta 记录 effects/transitions。xfade 链式拼接 3 clip 5.2s 验证通过
- **修正**：①flash 用 `fade=in` 纯色淡入（修 eq/drawbox 语法错）；②glitch 去 rgbashift（该 ffmpeg 无此滤镜）改 noise+hue 抖动；③wipe/zoom warp 表达式用 `t`/`on` 正确写法

**v6 prompt 多轮核实**（M1-M4 完成，2026-09-14；2026-09-15 修复多轮循环）：
- **M1 完整度评估** ✅：`llm.assess_completeness()` 6 维（主体/场景/情绪/风格/时长/镜头感）+ missing 列表 + 追问建议（异常兜底当 complete）。**口径**：subject/scene/emotion 必填需明确描述（仅名词隐含推测不算）；style/duration/shot 也纳入追问——**全 6 维齐才 COMPLETE**（prompt 靠多轮追问变长变详细）；temperature=0.1
- **M2 对话状态机** ✅：`utils/dialog.py`（DialogManager，OPEN/COLLECTING/COMPLETE/LOCKED，≤3 轮防死循环，SQLite `output/tasks.sqlite` dialogs 表）
- **M3 CLI `ask`** ✅：`vidance.py ask "概念"` 交互多轮追问 → 增强概念（`--out` 存文件）；**每轮把当前问题逐条问完合并成一条 reply**（`；`拼接）→ 一次重评；管道答案实测通过；`dm.locked_concept` 拼接轮次回答（"随便"跳过）
- **M4 API 三端点** ✅：`core/api_server.py` 加 `POST /api/dialog/start`（返回 missing/questions）+ `GET /api/dialog/{dialog_id}` + `POST /api/dialog/{dialog_id}/reply`（每轮返回增强 concept），供 v7 前端调用；start/reply curl 实测通过
- **修复（2026-09-15）**：①cmd_ask idx 跨轮累加导致只问一条就退出 → 重构为每轮批量问完再 reply；②选填维度被当必填判 → missing=[] 卡 COLLECTING → 只以必填维度判 COMPLETE；③评估口径"已隐含算不缺"→"明确描述才算"（temperature 0.3→0.1）；④停止条件过松（必填 3 维齐即 COMPLETE，2 问就停 prompt 长不起来）→ 改为**全 6 维齐才 COMPLETE**，style/duration/shot 也追问，概念经 2-3 轮逐步变长
- 不做自动补全（宁可多问）；≤3 轮 LOCKED 强制放行

**v7 前端 agent WebUI**（M1-M4 代码完成，2026-09-15；浏览器渲染+成片端到端待 GPU 恢复验证）：
- **M1 后端端点** ✅：`core/api_server.py` 加 `GET /api/video/{task_id}`（视频流 FileResponse）+ `GET /api/options`（音色/LUT/BGM/特效/运镜枚举）+ `GET /api/tools`（**15 命令**+参数 schema）+ `POST /api/tool/{name}`（统一分发，shlex 解析 `--key value`/`-n N`/位置参数）+ /ui StaticFiles；`scheduler.py _run_task` 加 **mode 分发**（auto/fast/hot 映射 vidance.py 子命令参数）；TaskSubmit 加 mode/images_source/images_count/effects/top_each/account 字段
- **M2 对话+/命令补全** ✅：`ui/index.html`（自包含 21KB，零 npm）三栏布局（左工具/任务边栏+顶部画布+右下发框）；`/` 弹层（↑↓/Tab/Enter 选择，参数区不弹）；自然语言 → v6 dialog
- **M3 画布** ✅：任务卡片 2s 轮询（进度条+stage+error）+ video 卡片（completed 自动嵌 `<video controls src=/api/video/{id}>`）+ 左栏任务列表（点击回看/轮询）
- **M4 ask+scout 接洽** ✅：/ask → 问题渲染 → 输入续聊（/api/dialog/{id}/reply）→ COMPLETE 后增强概念 chip 一键 `/fast`；scout 候选分数展示；voices/clip(异步轮询)/help 分发
- **验证**：后端 curl 实测（tools **15 命令** 全 dispatch /options/video 流 1.7MB/ask/voices/help/video 分发）；JS node --check 通过；/ui 200（21KB）；GPU 停机期间成片链路未测。新增查询/维护类命令全零 GPU（luts/moods/tasks/status/trends(爬热点不选题)/clean(默认 keep 10)）

**v1.1 改进**：
- **I2V 修复**：`WanImageToVideo` → `Wan22ImageToVideoLatent`（Wan 2.2 原生 48ch latent + noise_mask inpainting），首帧与参考图相关性 0.99+
- **character-mode 开关**：`auto`（默认，3DGS 审查不过自动降级 flux）/ `3dgs` / `mesh`（Hunyuan3Dv2→GLB→mesh_render）/ `flux`（每镜 FLUX 直接生成角色+场景图，不重建 3D）
- **3DGS 位姿修复**：PCA 自动对齐角色主轴到垂直 + 裁剪接地合成（脚踩地不悬浮）
- **3DGS 渲染优化**：飞点过滤 + 超采样渲染 + 大高斯参数，表面更平滑噪声更少
- **角色位姿修复**：FLUX 角色 prompt 强调 "standing upright on the ground"，审查加 pose 维度
- 不传 `--character` 时走 v0 纯 T2V

## 目录结构

```
vidance/
├── opencode.json              # opencode 配置（agents + permissions）
├── AGENTS.md                  # 本文件
├── config/config.json         # 运行时配置（API key、引擎地址、模型名、音色）
├── core/
│   ├── vidance.py             # 统一 CLI 入口（auto/custom/quick/concat 四子命令）
│   ├── pipeline.py            # auto 模式流水线主控（内部模块）
│   ├── custom_gen.py          # custom 模式 H3 ref2va 生成（内部模块）
│   ├── postprocess.py         # 共享后处理（RIFE/LUT/BGM/STT）
│   ├── scheduler.py           # v4 异步任务队列（TaskQueue SQLite + Scheduler 线程轮询）
│   ├── api_server.py          # v4 FastAPI 服务（:8894，任务提交/查询/仪表盘/选题/克隆/裁剪）
│   └── dashboard.py           # v4 HTML 仪表盘生成（队列统计+缩略图+选题批次）
├── utils/
│   ├── comfy_api.py           # ComfyUI HTTP 客户端（T2V/I2V/TripoSplat/FLUX T2I/H3 ref2va）
│   ├── llm.py                 # USTC LLM 客户端（编剧/prompt优化/审片，含图片缩放+重试）
│   ├── tts.py                 # TTS 客户端（双引擎：edge-tts + CosyVoice）
│   ├── tts_server.py          # TTS FastAPI 服务（双引擎，GPU2:9880）
│   ├── splat_renderer.py      # 3DGS PLY 多角度渲染器（render_splat_at_angle + composite_bg）
│   ├── ffmpeg_tools.py        # 抽帧/SRT/合成（含 LUT 调色 + BGM ducking）
│   ├── rife.py                # v2 RIFE 插帧客户端（镜头间过渡 + slowmo）
│   ├── gen_luts.py            # v2 生成 3D LUT .cube 文件（6 种风格）
│   ├── music.py               # v2 生成环境配乐 BGM（5 种风格，numpy 合成）
│   ├── stt.py                 # v2 faster-whisper STT 转写（字幕兜底）
│   ├── mesh_render.py         # v3 headless mesh 渲染器（pyrender+EGL，M3 已封装）
│   ├── hunyuan3d.py           # v3 Hunyuan3Dv2 重建客户端（单图+多视角→GLB，M5 已封装）
│   ├── asset_registry.py      # v3 资产库管理（CRUD+模糊搜索+CLI，M6 已封装）
│   ├── crawler.py             # v4 热点爬虫（B站API+微博+知乎+百度+RSS+yt-dlp，M1 已封装）
│   ├── voice_clone.py         # v4 声音克隆生产化（B站搜索+VAD切段+STT转写+音色注册，M3 已封装）
│   ├── funclip.py             # v4 FunClip 智能裁剪（FunASR字级时间戳+LLM语义keep/drop，M4 已封装）
│   ├── asset_crawler.py       # v4 素材爬取（Pexels 图源首选 + 必应兜底 + LLM关键词 + incompetech BGM，M5 已封装）
│   ├── fastline.py            # v4 M6 快速营销号链路（图片+zoompan 运镜+RIFE，跳过视频模型，M6 已封装）
│   ├── effects.py             # v5 剪辑特效（8 种镜头内特效 + xfade 链式转场，M1-M4 已实现）
│   ├── dialog.py              # v6 prompt 多轮核实状态机（DialogManager，SQLite，M1-M4 已实现）
│   ├── oneshot.py             # v8 一镜到底（链式 I2V 末帧回灌 + 运镜脚本 + 漂移控制；🎯 待实现）
│   ├── notify/                # v9 机器人网关（base/wecom/feishu/dingtalk/factory；🎯 待实现）
│   ├── luts/                  # v2 LUT 文件目录（cinematic/warm/cool/vintage/vivid/soft .cube）
│   └── workflows/
│       ├── wan_t2v.json       # Wan T2V workflow 模板
│       ├── wan_i2v.json       # Wan I2V workflow 模板（12 节点，Wan22ImageToVideoLatent）
│       ├── triposplat.json    # TripoSplat 单图→PLY workflow（13 节点）
│       ├── flux_t2i.json      # FLUX T2I workflow 模板（10 节点）
│       ├── rife_transition.json  # v2 RIFE 插帧 workflow（7 节点）
│       ├── hunyuan3d_single.json  # v3 Hunyuan3Dv2 单图→mesh workflow（M1 验证）
│       └── hunyuan3d_multiview.json  # v3 Hunyuan3Dv2 多视角→mesh workflow（M2 验证）
├── ui/                        # v7 前端（index.html 自包含，零 npm，:8894/ui 访问）
├── voices/                    # 自定义 CosyVoice 克隆音色素材目录
├── bgm/                       # v2 自定义 BGM 素材目录（放 {mood}.wav 自动使用）
├── .opencode/
│   ├── agents/{director,reviewer,asset,scout}.md
│   └── skills/{scriptwriting,review,topic_scouting}/SKILL.md
├── docs/                      # 设计文档（roadmap + v0-v9 design + deployed-models）
│   └── usage*.md              # 使用指南（usage.md 主入口 + usage-v1/v2/v3/v4/v6/v7.md 分版本小工具）
├── voice_samples/ → /mnt/dataset/...  # 音色试听样本（软链）
└── output/ → /mnt/dataset/... # 成片 + 元数据（软链到机械盘）
```

## 运行命令

### 统一入口 `core/vidance.py`（推荐）

四个子命令：`auto`（LLM 编剧+生成+后处理）、`custom`（参考图+脚本→H3→后处理）、`quick`（纯 T2V）、`concat`（多视频拼接）。

```bash
# ── auto：概念 → LLM 编剧 → Wan/FLUX 生成 → 后处理 ──
# v2 全功能：RIFE 过渡 + 并行预取 + LUT 调色 + BGM ducking + STT 字幕
python core/vidance.py auto "雪山日出：小狐狸的冒险" --character "红色小狐狸" --character-mode flux --stt

# auto 模式（默认 3DGS→flux 降级）
python core/vidance.py auto "一只猫在月球上跳舞" --character "穿宇航服的白猫"

# 指定调色风格和配乐 mood
python core/vidance.py auto "深海探险" --character "蓝色水母" --character-mode flux --lut cool --bgm mysterious

# 长片模式（动态镜头数，目标 60s → ~15 镜）
python core/vidance.py auto "深海探险" --character "蓝色水母" --character-mode flux --duration 60

# 全局慢动作（所有镜头 2x 慢放）
python core/vidance.py auto "概念" --character "角色" --slowmo 2

# 禁用部分功能
python core/vidance.py auto "概念" --character "角色" --no-rife --no-color --no-bgm

# ── custom：参考图 + 预写脚本 → H3 ref2va → 后处理 ──
python core/vidance.py custom --ref input/doubao.jpg --ref input/naiwa.jpg --script input/prompt1.txt

# custom + 后处理
python core/vidance.py custom --ref input/doubao.jpg --ref input/naiwa.jpg --script input/prompt1.txt --lut cinematic --bgm dramatic --stt

# custom 高保真模式（2048px 参考图，慢 2-3x）
python core/vidance.py custom --ref input/doubao.jpg --script input/prompt1.txt --ref-image-size max

# ── quick：纯 T2V，无角色无后处理 ──
python core/vidance.py quick "一只猫在月球上跳舞"

# ── concat：多视频拼接 ──
# 硬切拼接（ffmpeg stream copy，最快）
python core/vidance.py concat clip1.mp4 clip2.mp4 clip3.mp4 -o merged.mp4 --transition cut
# RIFE 光流过渡（需 GPU2 ComfyUI 8189 在线）
python core/vidance.py concat clip1.mp4 clip2.mp4 clip3.mp4 -o merged.mp4 --transition rife
# 交叉淡化
python core/vidance.py concat clip1.mp4 clip2.mp4 -o merged.mp4 --transition crossfade --crossfade-duration 0.5
```

**参数速查**：

| 参数 | auto | custom | quick | concat | 说明 |
|------|:----:|:------:|:-----:|:------:|------|
| `concept` (位置参数) | ✓ | — | ✓ | — | 视频概念（中文） |
| `videos` (位置参数) | — | — | — | ✓ | 待拼接视频文件列表 |
| `-o/--output` | ✓ | ✓ | ✓ | ✓ | 输出路径（相对→output_dir） |
| `--character` | ✓ | — | — | — | 角色描述（中文） |
| `--character-mode` | ✓ | — | — | — | auto/3dgs/mesh/flux |
| `--no-asset-reuse` | ✓ | — | — | — | 禁用资产库复用 |
| `--voice` | ✓ | — | ✓ | — | TTS 音色 |
| `--duration` | ✓ | — | — | — | 目标总时长（秒），动态镜头数 |
| `--slowmo` | ✓ | — | — | — | 全局慢动作倍率 |
| `--ref` | — | ✓ (可重复) | — | — | 角色参考图 |
| `--script` | — | ✓ | — | — | 分镜脚本路径 |
| `--ref-image-size` | — | ✓ | — | — | match/max |
| `--transition` | — | — | — | ✓ | cut/rife/crossfade |
| `--crossfade-duration` | — | — | — | ✓ | 交叉淡化时长 |
| `--lut` | ✓ | ✓ | — | — | LUT 风格 |
| `--no-color` | ✓ | ✓ | — | — | 禁用调色 |
| `--bgm` | ✓ | ✓ | — | — | BGM mood |
| `--no-bgm` | ✓ | ✓ | — | — | 禁用 BGM |
| `--no-rife` | ✓ | ✓ | — | — | 禁用 RIFE 过渡 |
| `--stt` | ✓ | ✓ | — | — | STT 字幕对齐 |

> 旧入口 `python core/pipeline.py ...` 和 `python core/custom_gen.py ...` 仍可用（向后兼容），但推荐使用 `vidance.py`。

### 启动 TTS 服务
```bash
conda activate cosyvoice
CUDA_VISIBLE_DEVICES=2 TTS_FP16=1 python utils/tts_server.py &
```

### 单步操作
```bash
# 编剧
python utils/llm.py --concept "概念"

# T2V 生成
python utils/comfy_api.py "english prompt" -o output/clips/shot_1.mp4

# TTS 配音（列出所有音色）
python utils/tts.py --list-voices

# TTS 配音（指定音色，edge-* 或 cosy-*）
python utils/tts.py "中文旁白" -v edge-moe -o output/clips/shot_1.wav

# 抽帧
python utils/ffmpeg_tools.py frames output/clips/shot_1.mp4 -n 4

# v4 热点爬虫
python utils/crawler.py --top 20
python utils/crawler.py --source bilibili --top 10
python utils/crawler.py --top 20 -o output/hot_topics.json

# v4 Scout 选题
python -c "
from utils.crawler import Crawler
from utils.llm import LLMClient
topics = Crawler().fetch_hot_topics(top_per_source=20)
candidates = LLMClient().scout_topics(topics, account_type='影视解说', n_candidates=5)
for c in candidates:
    print(f'[{c[\"predicted_score\"]}] {c[\"concept\"]}')
"

# v4 异步任务队列
# 启动 API server（含调度器，max_concurrent=1）
python core/api_server.py --port 8894

# 提交任务（CLI）
python core/scheduler.py submit "一只猫在月球上跳舞" --character "穿宇航服的白猫" --character-mode flux
# 提交任务（API）
curl -X POST http://127.0.0.1:8894/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"concept":"深海探险","character":"蓝色水母","character_mode":"flux"}'

# 查看队列
python core/scheduler.py list
curl http://127.0.0.1:8894/api/tasks | python -m json.tool

# 查看任务状态
python core/scheduler.py status <task_id>
curl http://127.0.0.1:8894/api/tasks/<task_id>

# 生成仪表盘
python core/scheduler.py dashboard -o output/dashboard.html
# 或访问 http://127.0.0.1:8894/api/dashboard

# v4 声音克隆生产化
# 搜索人声源（B站）
python utils/voice_clone.py search --keyword 新闻播报 --top 10
# 完整克隆流水线（搜索→下载→VAD切段→转写→注册→测试合成）
python utils/voice_clone.py clone --keyword 新闻播报 --name xinwen1 --desc "新闻播音-男声"
# 测试已注册音色（样本存 voice_samples/）
python utils/voice_clone.py test --name xinwen1
# 列出已注册音色
python utils/voice_clone.py list
# 热加载新音色（免重启 TTS server）
curl -X POST http://127.0.0.1:9880/voices/reload
# API 异步克隆（提交后轮询 /api/clone_voice/{job_id}）
curl -X POST http://127.0.0.1:8894/api/clone_voice \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"纪录片解说","name":"jilupian1","desc":"纪录片-男声"}'
```

## TTS 音色规范

音色按前缀路由引擎（服务在 9880）：

| 前缀 | 引擎 | 示例 |
|------|------|------|
| `edge-*` | edge-tts（微软在线，自然） | `edge-xiaoxiao` 晓晓 / `edge-moe` 萌系高音 / `edge-family` 家人们风 |
| `cosy-*` | CosyVoice（本地 GPU，可克隆） | `cosy-default` / `cosy-cross` |
| `cosy-<自定义>` | CosyVoice 自定义克隆 | `voices/<名>/prompt.wav` + `meta.json`（prompt_text/desc）自动注册，或 `utils/voice_clone.py clone` 全自动爬取克隆；新增后 `POST /voices/reload` 热加载 |

试听样本：`vidance/voice_samples/`（17 个 wav）。默认音色 `edge-moe`（config.json，萌系高音-哈基米风）。

## 服务端口

| 服务 | 端口 | GPU | 环境 |
|------|------|-----|------|
| MiniMax-H3 ref2va (ComfyUI) | 8188 | GPU0 | comfyui |
| Hunyuan3Dv2 turbo (ComfyUI) | 8193 | GPU1 | comfyui |
| Wan T2V/I2V + RIFE (ComfyUI) | 8189 | GPU2 | comfyui |
| HunyuanVideo (ComfyUI) | 8190 | GPU3 | comfyui |
| FLUX + TripoSplat (ComfyUI) | 8192 | — | comfyui |
| SDXL (ComfyUI) | 8191 | — | comfyui |
| TTS 双引擎 (edge-tts + CosyVoice) | 9880 | GPU2 | cosyvoice |
| Vidance API (任务队列 + 仪表盘 + v6/v7) | 8894 | — | comfyui |
| Vidance WebUI (v7 前端) | 8894/ui | — | comfyui（浏览器访问） |

> H3 (8188) 独占 GPU0，用于 custom 模式参考图条件视频生成。模型按需加载，首次 workflow 触发后占 ~46G。
> Hunyuan3Dv2 (8193) 独占 GPU1，v3 3D 重建引擎。
> Wan+RIFE (8189) 独占 GPU2，auto/quick 模式视频生成。
> HunyuanVideo (8190) 独占 GPU3，T2V 备选。
> FLUX(8192)/SDXL(8191) **已停**，需要时重启（FLUX 需找空卡或与 H3 互斥）。
> ⚠️ ComfyUI 启动**不能加 `--cuda-device`**，否则会覆盖 `CUDA_VISIBLE_DEVICES` 环境变量（main.py:87 逻辑）。正确做法：`CUDA_VISIBLE_DEVICES=N python main.py --listen 127.0.0.1 --port XXXX`。

## 关键约定

1. **随机 seed**：每次 T2V/I2V 生成必须用随机 seed（ComfyUI 缓存命中问题）
2. **每镜 ≤5s**：Wan 单次上限 121 帧≈5s@24fps
3. **旁白字数**：duration × 4 字（中文约 4 字/秒）
4. **审片阈值**：综合分 ≥7 通过，最多重试 2 次，取最高分兜底
5. **时长对齐**：成片按配音时长为准，画面不足定格末帧
6. **元数据完整**：每次任务记录 meta.json（脚本/prompt/seed/审片/时间戳）
7. **角色锚流程**（v1.1 + v3）：`--character` 传入角色描述 → FLUX 生角色参考图 → 按 `--character-mode` 分流：
   - `flux`：每镜 FLUX 直接生成角色+场景完整图 → Wan I2V（不重建 3D，质量最稳定）
   - `3dgs`：FLUX 图 → TripoSplat→3DGS → 每镜 RenderSplat 按角度渲染 → composite bg → Wan I2V（所有镜头强制 3DGS）
   - `mesh`（v3）：FLUX 图 → Hunyuan3Dv2→GLB → 每镜 mesh_render 按角度渲染 → composite bg → Wan I2V（几何更准，可导出）
   - `auto`（默认）：先走 3DGS 流程 + review_character 审查，不过则自动降级 flux
   - **资产复用**（v3 M6）：mesh/3dgs 模式重建前先查资产库（相似度≥0.6 + score≥7），命中则跳过重建直接渲染预览；重建后 review≥7 自动入库。`--no-asset-reuse` 可禁用
8. **审片 5 维**（v1）：consistency/quality/motion/artifact/character_consistency，与角色参考图对比
9. **审片图片缩放**：上传前 resize 到 512px + JPEG 85%（减少 payload，加快 API 响应）
10. **审片超时处理**：180s timeout + 2 retries，失败则 safe fallback auto-pass（不阻塞流水线）
11. **RIFE 过渡**（v2）：镜头间提取首尾帧 → RIFE 光流插帧（multiplier=8）→ 0.375s 过渡片段，config `rife.enabled`
12. **镜头并行预取**（v2）：ThreadPool 并行预取 FLUX 参考帧 + TTS 配音，与 Wan I2V 串行不冲突
13. **LUT 调色**（v2）：6 种 3D LUT 程序生成（`gen_luts.py`），LLM 自动选风格，ffmpeg `lut3d` 滤镜，config `color.enabled`
14. **BGM ducking**（v2）：5 种 BGM 程序合成（`music.py`），ffmpeg `sidechaincompress` 配音时自动降 BGM，config `bgm.enabled`
15. **STT 字幕兜底**（v2）：faster-whisper large-v3-turbo 转写成片音频→SRT→重新烧录，`--stt` 开关，默认关闭（TTS 时间戳通常够用）

## Python 环境

- **主环境 (comfyui)**：`/home/zxy/.conda/envs/comfyui/bin/python`（torch 2.13+cu130）
  - 用于：pipeline、comfy_api、llm、ffmpeg_tools、moviepy、rife、stt、music、gen_luts、**mesh_render（v3: trimesh+pyrender+EGL）**
- **TTS 环境 (cosyvoice)**：`/home/zxy/.conda/envs/cosyvoice/bin/python`（torch 2.3.1+cu121）
  - 用于：tts_server（CosyVoice 推理）
- **STT 模型**：`/mnt/dataset/zxy/hf_cache/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/`（int8_float16 GPU，HF_HOME 指向 hf_cache）
- **v3 headless 渲染依赖**（comfyui 环境）：trimesh 5.1.0 + pyrender 0.1.45 + PyOpenGL 3.1.0，EGL 后端（`PYOPENGL_PLATFORM=egl`）

## LLM 模型

| 模型 | 用途 |
|------|------|
| deepseek-v4-flash | 编剧、prompt 优化、LUT 风格选择、BGM mood 选择 |
| qwen3.8-chat | 多模态审片（主力，~30-120s/镜，支持视觉） |
| deepseek-v4-flash | 编剧、prompt 优化、LUT/BGM 选择（纯文本，不支持视觉） |

## 已知限制（v2 + v3）

1. **3DGS 角色重建质量仍是瓶颈**（character_consistency 2-4/10）：
   - TripoSplat 262K 高斯渲染稀疏，像素覆盖 31-35%（v1.1 位姿修复后提升，原 13-26%）
   - 3DGS 颜色偏暗（mean 0.51 vs FLUX 原图 0.94），宇航服细节丢失
   - **v1.1 位姿修复**：`load_ply` PCA 自动对齐角色主轴到垂直（替代手动 Y/Z 交换），`composite_bg` 裁剪角色 bbox → 缩放 55% 画面高 → 接地放置 88% 位置（脚踩地不悬浮）
   - **v1.1 I2V 修复**：Wan22ImageToVideoLatent（48ch + noise_mask），首帧与参考图相关性 0.99+
   - **v1.1 缓解**：`flux` 模式跳过 3D 重建；`auto` 模式 3DGS 审查不过自动降级 flux
   - **v1.1 渲染优化**：`_filter_floaters` 过滤孤立高斯（距离>99th pct + opacity<0.03），渲染参数 min_px 3→5、gain 2→3、supersample=2（2x 渲染→LANCZOS 缩放），LLM 确认"噪声显著减少，表面更平滑"
   - **剩余问题**：3DGS 镜头类型受限（只能全身远景/中景，无法特写），部分角度重建质量不均
   - **后续**：v2 探索多图 3D 重建 / 参考图 ControlNet / IP-Adapter 角色锁定 / 表面平滑
2. **flux 模式角色一致性依赖 FLUX prompt**：侧面/背面镜头（yaw≠0）FLUX 生成角色可能走样，无 3D 约束
3. **多模态审片 API 延迟波动大**（12s-200s+）：
   - USTC qwen3.8-chat 多模态调用延迟 30-120s/镜，已加 180s timeout + 2 retries + safe fallback
   - 多数审片实际 auto-pass（超时降级），仅前 2 次成功返回详细反馈
4. **review_character 超时**：4 张 512×512 预览图 + 1 ref，payload 较大，通常超时 auto-pass
 5. **BGM 已修复为真实素材**（2026-09-13 修复）：原 numpy 程序合成（music.py）有两个问题——①归一化 bug `wave/max(1,np.max(...))*0.8` 用 `max(1,...)` 钳制分母导致峰值锁死 -18dB；②calm/mysterious/dramatic 设计成超低频 drone（55-82Hz），人耳+普通喇叭听不见（放大音量也无济于事）。**修复**：①`music.py` 归一化改为 `wave/(np.max(np.abs(wave))+1e-9)*0.8`；②从 incompetech.com（Kevin MacLeod，CC BY 免版权，直连可达）下 5 首真实纯音乐经 loudnorm 归一到 I≈-20 LUFS 存 `bgm/{calm,uplifting,mysterious,dramatic,playful}.wav`，`select_bgm` 自动优先使用 custom_dir（无需改代码）。注意 `bgm/` 是真实长曲（59-261s），compose 会按视频时长截断；如需换曲直接替换 `bgm/{mood}.wav`（响度建议 I≈-20 LUFS）
6. **LUT 为程序生成**（v2）：numpy 生成 6 种风格 3D LUT，效果不如专业 LUT 包，留 `color.lut_dir` 自定义路径
7. **STT 默认关闭**（v2）：TTS 时间戳通常够用，`--stt` 仅在需要重新对齐时开启（额外 GPU 显存+耗时）
8. **视频编码统一 yuv420p**（v2 修复）：LUT 调色 + STT 烧录的 ffmpeg 命令均加 `-pix_fmt yuv420p`，确保所有播放器兼容（之前 lut3d 滤镜导致输出 yuv444p，部分播放器无法播放）
9. **~~长片未验证~~ → 已实现**（v2.1）：`--duration N` CLI 动态调整 LLM 编剧镜头数（`target_duration/4` 镜），解锁长片能力
10. **~~单镜慢动作未接入~~ → 已实现**（v2.1）：`--slowmo N` 全局慢动作 + LLM 自动标注 `shot.slowmo`，pipeline 调用 `RIFEClient.slowmo()` 帧倍增
11. **~~per-shot 过渡类型未实现~~ → 已实现**（v2.1）：LLM 标注 `shot.transition_out: "rife"|"crossfade"|"cut"`，pipeline 按类型分流过渡，compose 支持混合过渡
12. **后期审片 4 维**（v2.1 跳过）：API 不稳定，投入产出比低，保留逐镜审片
13. **music subagent 简化**（v2 偏差）：设计 §8.2 定义独立 music subagent，实际简化为 pipeline 内联 LLM 调用（`llm.select_bgm_mood()`），功能等价
14. **~~无 `--duration` CLI~~ → 已实现**（v2.1）：`--duration` 已加入 auto 子命令
15. **concat 子命令**（v2.1 新增）：`vidance.py concat` 支持多视频拼接（cut/rife/crossfade），可复用于已有素材合并
16. **v3 M1 mesh 为 non-watertight**（v3）：Hunyuan3Dv2 turbo 输出 mesh 为 non-watertight（VoxelToMesh surface net 特性），不影响渲染但影响后续物理仿真/布尔运算。M2 多视角重建改善：主连通分量 38%→63%，bbox 比例更自然（aspect 1.02→1.69）
17. **v3 pyrender 材质处理**（v3，✅ M3 已处理）：GLB 的 PBR 材质在 pyrender 可能部分丢失，M3 封装时已加 3-point lighting（key+fill+ambient）和 `gray_fallback` 选项（覆盖为灰色材质用于预览审查）
18. **~~v3 Hunyuan3Dv2 与 Wan 共实例~~ → 已分离**（v3）：Hunyuan3Dv2 已独立到 GPU1:8193，不再与 Wan 共用 8189，3D 重建与视频生成可并行

# v4 FunClip 智能裁剪（长素材语义裁剪，device='auto' 自动选卡）
# 转写显示字级时间戳
python utils/funclip.py transcribe output/clips/narration.wav
# LLM 语义裁剪（一句话描述保留什么）
python utils/funclip.py smart long.wav -i "只要讲龙的部分，去掉口误" -o dragon.wav
# API 异步裁剪（提交后轮询 /api/clip/{job_id}）
curl -X POST http://127.0.0.1:8894/api/clip \
  -H 'Content-Type: application/json' \
  -d '{"input":"/path/long.wav","instructions":"只要讲龙的部分","output":"dragon.wav"}'

# v4 M5 素材爬取（参考图 + BGM）
# LLM concept → 图片搜索关键词
python utils/asset_crawler.py keywords "雪山上的日出小狐狸"
# concept → 关键词 → 图片下载（一条龙）
python utils/asset_crawler.py refs "雪山上的日出小狐狸" -o output/ref/ --top 2
# BGM：列候选 / 下载（→ bgm/{mood}.wav，--bgm epic 直接生效）
python utils/asset_crawler.py bgm epic --list
python utils/asset_crawler.py bgm epic

# v4 M6 快速营销号链路（图片+zoompan+RIFE，跳过视频模型）
# 概念 → LLM 旁白 → 出图 → KenBurns 伪动态 → TTS → crossfade → LUT/BGM/STT
python core/vidance.py fast "雪山日出小狐狸" -n 5                        # 自动爬图+校验+自动 BGM/LUT
python core/vidance.py fast "概念" --images-source flux -n 5              # FLUX 逐段文生图
python core/vidance.py fast "概念" --bgm epic --lut warm --stt --slowmo 2 # 指定 BGM/LUT + STT + RIFE 慢放
python core/vidance.py fast "概念" --source-topic "热搜标题" --trend-date 2026-09-14  # 热搜角标+meta

# ✅ 固定流程（一步到位）：爬实时热点 → LLM scout 选题 → 出片
python core/vidance.py hot -n 5 --top-each 10 --account "影视解说"

# v5 剪辑特效（--effects auto 由 LLM 逐段选特效）
python core/vidance.py fast "概念" --images dir/ --effects auto       # LLM 选 glitch/颗粒/定格等
python utils/effects.py apply in.mp4 --effect punch_in -o out.mp4     # 单片段特效
python utils/effects.py transition a.mp4 b.mp4 --kind slide -o out.mp4 # xfade 双段拼接

# v6 prompt 多轮核实（信息不全时追问，增强后概念可喂 auto/fast）
python core/vidance.py ask "深海探险"                 # 交互多轮追问（≤3 轮），--out 存增强概念
python core/vidance.py ask "深海探险" --out input/concept.txt
# dialog API（v7 前端用）
curl -X POST http://127.0.0.1:8894/api/dialog/start -H 'Content-Type: application/json' -d '{"concept":"深夜灯塔"}'
curl -X POST http://127.0.0.1:8894/api/dialog/dlg_xxx/reply -H 'Content-Type: application/json' -d '{"text":"暴风雨夜，灯塔守护人独自值班"}'

# v7 前端 agent WebUI（浏览器访问 http://127.0.0.1:8894/ui）
# 左栏工具/任务边栏 + 顶部画布 + 右下发框；/ 弹命令补全
# /fast 概念 -n 3 --effects auto   → 提交任务 → 画布轮询 → 成片 video 卡片
# /hot /auto /ask /clip /scout /voices /video {id} /help 同理
# 后端对应：
curl http://127.0.0.1:8894/api/tools                     # 15 命令 + 参数 schema
curl http://127.0.0.1:8894/api/options                   # 音色/LUT/BGM/特效枚举
curl -X POST http://127.0.0.1:8894/api/tool/fast -H 'Content-Type: application/json' -d '{"args":"概念 -n 3 --effects auto"}'
curl http://127.0.0.1:8894/api/video/{task_id} -o final.mp4   # 成片视频流

# ── 已规划（🎯 待实现，见 docs/v7-design.md ~ v8-design.md）──
# v8 一镜到底（链式 I2V 拼缝隐形，30s+）
# python core/vidance.py auto "穿越隧道的光" --oneshot --duration 30
