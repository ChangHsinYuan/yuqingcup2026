# Vidance v7 使用指南 — 前端 agent WebUI

> v7 前端使用方法。设计见 [v7-design.md](./v7-design.md)

## 快速开始

```bash
# 1) 启动 API server（已含前端静态服务 + 调度器）
cd /mnt/disk_sdb/zxy/vidance
python core/api_server.py --port 8894

# 2) 浏览器打开
http://127.0.0.1:8894/ui
```

零 npm/零构建：`ui/index.html` 自包含（HTML+vanilla JS，21KB），FastAPI StaticFiles 直接托管。

## 界面布局（仿豆包/智象未来）

```
┌───────────────────────────────────────────────┐
│ 顶部：Vidance 品牌 + 会话标题 + ＋新建             │
├──────────┬────────────────────────────────────┤
│ 左栏     │  画布区（大片留白）                    │
│ ├ 工具/命令│  - 用户消息（右侧蓝气泡）+ 机器人回复     │
│ │ /auto  │  - 任务卡片（进度条轮询）              │
│ │ /fast  │  - 成片 video 卡片                   │
│ │ /hot … │  ────────────────────────────────  │
│ ├ 任务列表 │  右下发框：输入框 + ↑发送 + / 补全弹层   │
└──────────┴────────────────────────────────────┘
```

- 窄屏（<768px）左栏折叠，☰ 按钮唤出
- 输入 `/` 弹命令补全（↑↓ 选择 / Tab·Enter 确认），进参数区后不再弹
- 左栏"任务"列表点击 → 画布回看（completed 嵌 video，运行中拉起轮询）

## / 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/fast 概念 [参数]` | 快速营销号链路（爬图+运镜+TTS） | `/fast 深夜灯塔的故事 -n 3 --effects auto` |
| `/hot [参数]` | 爬热点→选题→出片一步到位 | `/hot -n 5 --account 影视解说` |
| `/auto 概念 [参数]` | 完整流水线（LLM 编剧+视频模型） | `/auto 深海探险 --character 蓝色水母` |
| `/ask 概念` | v6 多轮核实（问答补全概念） | `/ask 深夜灯塔` |
| `/scout` | 爬热点→概念候选排序 | `/scout --account 萌宠` |
| `/clip 路径 -i 指令` | FunClip 语义裁剪 | `/clip /path/x.wav -i "只要讲光的部分"` |
| `/voices` | 列已注册音色 | `/voices` |
| `/video {task_id}` | 回看成片 | `/video 20260915_...` |
| `/help` | 列全部命令 | `/help` |

参数支持 `--key value`、短参 `-n 3`、flag（`--stt`）；`-n` 上限 5（图片张数约定）。

## 对话式出片（自然语言 → /ask → /fast）

1. 直接输入概念（如"深夜灯塔"）→ 自动走 v6 多轮核实：机器人列缺失项 + 追问
2. 直接回答（或"随便"跳过）→ 每轮重新评估 → 全 6 维齐 COMPLETE
3. 补全后点 **→ /fast 出片** chip → 自动填入 `/fast 增强概念` → 提交
4. 画布任务卡片 2s 轮询进度 → completed 自动嵌 `<video>` 播放

## 后端端点（前端依赖的 API 契约）

```
GET  /api/tools               # [{name, help, params[]}] 15 命令
GET  /api/options             # {voices, luts, bgms, effects, motions, ...}
GET  /api/video/{task_id}     # 成片视频流（FileResponse final.mp4）
POST /api/tool/{name}         # 统一分发 body: {args: "概念 --key value"}
POST /api/tasks               # 任务提交（/auto /fast /hot 底层，options.mode 区分）
GET  /api/tasks/{id}          # 轮询进度（status/stage/progress/output）
POST /api/dialog/start|{id}/reply   # v6 对话（/ask 与自然语言底层）
POST /api/clip  GET /api/clip/{id}  # FunClip 异步
POST /api/scout               # 选题候选
```

任务队列 mode 分发：`POST /api/tasks` 的 `options.mode` = auto|fast|hot，scheduler 按模式映射 vidance.py 子命令参数（`core/scheduler.py _run_task`）。

## 验证记录（2026-09-15）

- 后端 curl 实测：/api/tools（**15 命令**，含新增 luts/moods/tasks/status/trends/clean）、/api/options（含 3 个克隆音色）、POST /api/tool/help|voices|video|ask 分发正确、GET /api/video/{id} 200（1.7MB mp4）、/ui 200（21KB）
- JS 语法：node --check 通过（提取 script 段）
- 编译：py_compile api_server.py scheduler.py 通过
- **全服务关停联测**（GPU 暂停给师兄用）：POST /api/tool/fast 提交 → scheduler mode=fast 分发 → 爬图照常（Pexels 在线不占 GPU）→ TTS 缺省（静音兜底 + ⚠ 打印）→ **6.0s 成片 completed** → /api/video 流 200（409KB）→ /api/tool/video 返回 video_url。前端三类卡片数据形态齐验：任务卡片（queued/running/failed + error）、video 卡片（completed + <video src=/api/video/{id}>）、dialog 卡片（/ask missing/questions/续聊/COMPLETE chip）
- **待验证**（GPU 恢复后补）：浏览器实际渲染 + 真声 TTS /fast 全流程出片
