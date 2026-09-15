Goal
- 完成 Vidance 流水线 v4(M6 快速营销号链路)+v5(剪辑特效)+v6(prompt 多轮核实) 实现与文档；细化 v7(仿豆包 agent WebUI)、v8(一镜到底)、v9(机器人网关多 IM) 文档；当前收尾 v6 M3/M4 并同步文档
Constraints & Preferences
- 代码在 /mnt/disk_sdb/zxy/vidance；输出走 config output_dir=/mnt/dataset/zxy/vidance/output
- 视频编码必须 yuv420p h264；单镜 ≤5s（Wan 121 帧上限）
- 仓库编号统一（v4/v5/v6/v7/v8/v9），已删除所有“用户编号”对照行
- README.md 禁 AI 改；aluupy 训练不能动；Qwen 8000 不需要；验证要老实、不空转、不编造
- BGM 用 incompetech CC BY（bgm/{mood}.wav custom_dir 优先，已含 epic）
- 不自动发布：成片人工精挑细选手动上传（发布模块已删）
- 前端零 npm 自包含 HTML+vanilla JS；v7 仿豆包/智象未来（左栏+顶部画布+右下发框+/斜杠命令调 tool）；v9 机器人网关多 IM（企微/飞书/钉钉/Slack/Telegram，个人微信不做）
- 用户要爬图（不要纯 FLUX），要图对题、快、数量 ≤5、去重、带 BGM；FLUX 仅辅助补帧/兜底
- 用户不接受空转/长时间无产物；“继续”时须直接出结果


Progress


Done
- v4 M6 快速营销号链路 ✅：utils/fastline.py（FastLine：plan_narration LLM 拆 N 段旁白+运镜 pan/zoom-in/zoom-out → collect_images → _kenburns ffmpeg zoompan 伪动态 → 逐段 TTS → crossfade 拼接 → LUT/BGM/STT 复用 PostProcessor）+ vidance.py fast/hot 子命令；--slowmo N 另出 final_slow.mp4（原片保留）；--source-topic/--trend-date 写 meta+顶部 drawtext 角标（不占字幕时间轴）；hot=爬热点→scout 自动选题→fast 一步到位；FLUX 生图质量门（optimize_fastline_prompt 中文旁白→英文 prompt + assess_image_relevance 审核不过重生成≤2）；P 模式修复（_image_to_base64 支持 RGBA/P/LA/PA→RGB）
- 图源根治 ✅：必应 images（murl=来源页配图无关、CDN 缩略图杂，实测命中率 0-25%，混入泰姬陵/日本出生率/雪山）→ 改 Pexels 图库 API（config.json pexels.api_key=F5CXcrHXGjKi2ap4msh6hi8CITKYsP0TJzwrWtbCXm2CgNWJX30oWw6W 用户自供）；中文自动 LLM 英译（_en_translate）；search_images Pexels 首选必应兜底；collect_images crawl 直取 n 张（≤5，md5 去重，去掉逐张慢速多模态校验→19s 出片）
- v5 剪辑特效 ✅ M1-M4：utils/effects.py（8 特效 flash/punch_in/glitch/grain_vignette/speed_ramp/freeze_zoom/wipe/zoom + transition()+concat_with_transitions() 链式 xfade 30+ 种）+ llm.select_effects() + fast/hot --effects auto；修 flash/glitch/wipe 滤镜语法；端到端验证（4 镜 glitch/grain_vignette/speed_ramp/freeze_zoom 33s 出片）
- v6 M1-M4 全部完成 ✅：M1 llm.assess_completeness()（6 维主体/场景/情绪/风格/时长/镜头感）；M2 utils/dialog.py（DialogManager 状态机 OPEN/COLLECTING/COMPLETE/LOCKED，≤3 轮，SQLite output/tasks.sqlite dialogs 表）；M3 vidance.py ask 子命令（交互多轮→增强概念，测试通过）；M4 api_server.py 三端点 POST /api/dialog/start+GET /api/dialog/{id}+POST /api/dialog/{id}/reply（curl 实测 start 返回 missing/questions，端口 8994 测试通过）；reply 已改为每轮返回增强 concept
- 文档全部同步 ✅：docs/v5-design.md~v9-design.md 全写（v5 状态✅M1-M4；v6 状态🚧M1-M2 行待改✅）；v7-design.md 仿豆包 webUI（布局图+slash 命令表+/api/tools+POST /api/tool/{name}）；v9-design.md 多网关（Notifier 抽象+wecom/feishu/dingtalk/slack/telegram+factory 多通道+回调网关）；roadmap.md（v5✅、v6🚧M1-M2、v7 细化、v9 段+目录树）；AGENTS.md（状态行、目录树加 dialog.py/notify/、asset_crawler 描述改 Pexels）；usage-v4.md（M6 章节+参数表）
- 成片验证：output/fvquick（19.4s 爬图+特效）、fvbgm（BGM calm）、fvfx2（4 镜 12.7s 四种特效 33s）、fvdemo、fhot_v2 等均在 /mnt/dataset/zxy/vidance/output/


In Progress
- v6 收尾文档：v6-design.md 状态头改✅M1-M4（正要编辑时空转被打断）；AGENTS.md/roadmap.md v6 状态 M1-M2→M1-M4 完成；AGENTS 命令区加 ask 示例
- 清理 tools/src_probe.py、tools/sm_probe.py 临时探测脚本（可留作诊断工具）


Blocked
- (none)

- v3 headless mesh 渲染方案待验证


Key Decisions
- 图源：必应废弃（返回杂图不可救），Pexels 为准（英文库需中文先 LLM 英译），FLUX 仅兜底补帧
- 爬图去掉逐张多模态相关性校验（Pexels 本身精准）→ 解决慢；FLUX 生图保留审核门（重生成≤2）
- 执行顺序：v4→v5→v6（M1-M4 完成）→v7（前端，依赖 v6 API 已就绪）→v8（一镜到底）→v9（机器人网关）
- v9 统一 Notifier 抽象+factory 多通道（一次推多个 IM），回调复用 v7 /api/tool/{name}，M4 依赖 v7
- 版本编号统一仓库口径，用户确认“以后统一按你这个编号来”


Next Steps
1. v6-design.md 状态头改「✅ M1-M4 完成」+ AGENTS.md/roadmap.md v6 状态更新 + AGENTS 命令区加 ask 示例
2. 向用户总结 v6 完成情况（ask 交互演示 + dialog API curl 证据）
3. 按序实现 v7 前端（M1 骨架：静态服务+三栏+/api/tools+POST /api/tool/{name}；v6 dialog API 已就绪可接）
4. 之后 v8 一镜到底、v9 机器人网关（需用户提供企微/飞书 webhook）



Critical Context
- Pexels key 在 config.json pexels.api_key；中文 keyword 直查 Pexels 会 0 结果（必须 _en_translate）；返回 large2x/medium
- 必应坑（如复用）：murl=来源页配图常无关；turl+md5=CDN 缩略图仍杂；images/search 页 35 条 iusc（m="..." JSON）；async 接口时好时坏
- hot 子命令无 --no-bgm/--no-color 参数（会 unrecognized arguments）；fast 有全套
- dialog API 测试：POST /api/dialog/start {"concept":"深夜灯塔"} 返回 {"dialog_id":"dlg_...","status":"OPEN","dimensions":{...},"missing":["场景/环境","情绪基调"],"questions":[...]}
- v5 修复：flash=fade=t=in纯色淡入；glitch 去 rgbashift（ffmpeg 无此滤镜）改 noise+hue；wipe/zoom 用 t/on 表达式
- 后台长任务用 setsid bash -c '...' & disown；pgrep -f <name> 会误匹配自身查询命令（PID 每次变，用 ps aux | grep "[v]idance.py"）
- FLUX 生成慢（每张 1-3 分钟）；hot -n 2 --images-source flux 约 200s；hot -n 3 会超 400s 工具超时
- GPU：8188(H3 GPU0)/8189(Wan+RIFE GPU2)/8190(HunyuanVideo GPU3)/8192(FLUX+TripoSplat)/8193(Hunyuan3Dv2 GPU1)/9880(TTS GPU2)/8894(Vidance API)
- USTC LLM sk-M4b0y-Euavlan2tL6ZcWfA（deepseek-v4-flash / qwen3.8-chat）；主 Python /home/zxy/.conda/envs/comfyui/bin/python；4× RTX 4090



Relevant Files
- /mnt/disk_sdb/zxy/vidance/utils/fastline.py — v4 M6 FastLine（collect_images/plan_narration/_kenburns/_gen_flux_images/run，effects 参数，_overlay_source）
- /mnt/disk_sdb/zxy/vidance/utils/asset_crawler.py — Pexels 首选+必应兜底（_pexels_search/_en_translate/_search_bing/download_images）
- /mnt/disk_sdb/zxy/vidance/utils/effects.py — v5 特效库（8 特效+transition/concat_with_transitions+CLI）
- /mnt/disk_sdb/zxy/vidance/utils/dialog.py — v6 DialogManager（start/reply/get/locked_concept/list，reply 每轮返回 concept）
- /mnt/disk_sdb/zxy/vidance/utils/llm.py — assess_completeness/select_effects/optimize_fastline_prompt/assess_image_relevance/_image_to_base64(P 模式修复)
- /mnt/disk_sdb/zxy/vidance/core/vidance.py — auto/custom/quick/concat/hot/fast/ask 子命令
- /mnt/disk_sdb/zxy/vidance/core/api_server.py — 已加 v6 dialog 三端点（start/get/reply）
- /mnt/disk_sdb/zxy/vidance/config/config.json — pexels.api_key + llm/tts/comfyui
- /mnt/disk_sdb/zxy/vidance/docs/v6-design.md — 状态头待改✅M1-M4（其余内容已齐）
- /mnt/disk_sdb/zxy/vidance/docs/v7-design.md — 仿豆包 webUI 细化版（布局图+slash 命令表）
- /mnt/disk_sdb/zxy/vidance/docs/v9-design.md — 多网关版（Notifier/wecom/feishu/dingtalk/factory）
- /mnt/disk_sdb/zxy/vidance/docs/roadmap.md — v5✅/v6🚧(待改✅)/v7 细化/v8/v9 段+目录树
- /mnt/disk_sdb/zxy/vidance/AGENTS.md — 状态行/目录树（dialog.py/notify/ 已加；v6 状态待改 M1-M4）
- /mnt/disk_sdb/zxy/vidance/docs/usage-v4.md — M6 章节+参数表（--effects 行已加）
- /mnt/disk_sdb/zxy/vidance/tools/src_probe.py、tools/sm_probe.py — 图源探测脚本（临时诊断用）
- /mnt/dataset/zxy/vidance/output/fvquick|fvbgm|fvfx2|fvdemo|fhot_v2/ — 成片验证目录

