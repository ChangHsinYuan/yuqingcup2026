# Vidance v4 使用指南 — 营销号流水线小工具

> v4 三个里程碑（M1 爬虫选题 / M2 异步任务队列 / M3 声音克隆）的逐个小步骤使用方法。
>
> 主入口使用见 [usage.md](./usage.md)，设计见 [v4-design.md](./v4-design.md)

## 总览

| 里程碑 | 工具 | 一句话 |
|--------|------|--------|
| M1 | `utils/crawler.py` | 多源热点爬取（B站/微博/知乎/百度/RSS/YouTube） |
| M1 | `utils/llm.py scout_topics()` | 热点 → 视频概念候选排序 |
| M2 | `core/scheduler.py` | 任务队列 CLI（提交/查看/取消/仪表盘） |
| M2 | `core/api_server.py` | FastAPI REST 服务（:8894） |
| M2 | `core/dashboard.py` | HTML 仪表盘生成 |
| M3 | `utils/voice_clone.py` | 爬人声 → 自动克隆 CosyVoice 音色 |

---

## M1 — 热点爬虫（utils/crawler.py）

### CLI

```bash
cd /mnt/disk_sdb/zxy/vidance

# 4 源全爬（B站/微博/知乎/百度，每源前 20 条），终端打印
python utils/crawler.py --top 20

# 只爬一个源
python utils/crawler.py --source bilibili --top 10
python utils/crawler.py --source weibo --top 15

# 爬取并保存 JSON（供程序用 / 归档）
python utils/crawler.py --top 20 -o output/hot_topics.json

# YouTube 热门（需能访问 YouTube，可能需代理）
python utils/crawler.py --source youtube --top 20
```

### Python API

```python
from utils.crawler import Crawler

crawler = Crawler()

# 全源爬取
topics = crawler.fetch_hot_topics(top_per_source=20)
# → [{title, source, heat, url, snippet, rank, extra}, ...]

# 指定源
topics = crawler.fetch_hot_topics(sources=['bilibili', 'weibo'], top_per_source=15)

# YouTube 热门（yt-dlp 元数据，无下载）
topics = crawler.fetch_youtube_trending(region='CN', top=20)

# 通用 RSS 订阅
topics = crawler.fetch_rss('https://example.com/rss', source_name='mysource', top=20)

# 保存 JSON
crawler.save_topics(topics, 'output/hot_topics.json')
```

### 返回字段

| 字段 | 说明 |
|------|------|
| `title` | 标题 |
| `source` | `bilibili`/`weibo`/`zhihu`/`baidu`/`rss`/`youtube` |
| `heat` | 热度数值（B站=播放量，微博/知乎/百度=热度指数，RSS=排名） |
| `url` | 原帖链接（知乎/百度部分为空） |
| `snippet` | 摘要（80-120 字） |
| `rank` | 源内排名 |
| `extra` | 附加信息（UP主/标签/heat_text 等） |

> **注意**：`heat` 不同源不可比（B站播放量 vs 微博热度指数），排序只在源内有意义。

---

## M1 — LLM 选题（scout_topics）

热点 → 视频概念候选，按预测潜力打分排序。

```bash
# 快捷方式（Python 一行）
python -c "
from utils.crawler import Crawler
from utils.llm import LLMClient
topics = Crawler().fetch_hot_topics(top_per_source=20)
candidates = LLMClient().scout_topics(topics, account_type='影视解说', n_candidates=5)
for c in candidates:
    print(f'[{c[\"predicted_score\"]}] {c[\"concept\"]}')
    print(f'    角度: {c[\"angle\"]}')
    print(f'    理由: {c[\"reason\"]}')
"
```

### 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `hot_topics` | 爬虫返回的热点列表 | 必填 |
| `account_type` | 账号定位：`影视解说`/`萌宠`/`情感`/`科技科普` 等 | `''` |
| `n_candidates` | 生成候选数 | `5` |

### 返回字段

```json
{
  "concept": "一只橘猫用擦丝器把土豆擦成旋风薯塔...",   // 视频概念（喂给 vidance auto）
  "angle": "从猫咪视角演绎美食制作",                    // 创意角度
  "reason": "结合擦丝器薯塔热点，萌宠演绎易传播",        // 推荐理由
  "predicted_score": 9,                                // 预测潜力 1-10
  "source_topic": "用擦丝器解锁旋风薯塔"                // 关联热点
}
```

### scout subagent（对话式）

在 opencode 会话里直接让 scout subagent 选题（走 `.opencode/agents/scout.md`），或参考 `.opencode/skills/topic_scouting/SKILL.md` 规范。

---

## M2 — 任务队列 CLI（core/scheduler.py）

### 提交任务

```bash
# 基本（concept 必填，其余可选）
python core/scheduler.py submit "一只猫在月球上跳舞"

# 带角色 + flux 模式 + 音色
python core/scheduler.py submit "深海探险" \
    --character "蓝色水母" --character-mode flux --voice edge-moe

# 全参数
python core/scheduler.py submit "雪山日出" \
    --character "红色小狐狸" \
    --character-mode flux \
    --duration 30 \
    --lut cool --bgm calm
```

### 查看队列

```bash
# 列出任务（默认 50 条）
python core/scheduler.py list
python core/scheduler.py list --status running --limit 10

# 单任务状态（含 progress/stage/error）
python core/scheduler.py status 20260912_200827_7138
```

### 取消任务

```bash
python core/scheduler.py cancel 20260912_200827_7138   # 仅 queued 可取消
```

### 生成仪表盘（离线）

```bash
python core/scheduler.py dashboard -o output/dashboard.html
```

### 独立调度器（一般不用，API server 内置）

```bash
# 仅当不想跑 API server 时，单独跑 CLI 调度器轮询队列
python core/scheduler.py run --max-concurrent 1 --poll-interval 5
```

> ⚠️ 不要同时跑 CLI 调度器和 API server（双调度器会争抢任务）。

---

## M2 — API 服务（core/api_server.py）

### 启动

```bash
python core/api_server.py --port 8894
# 启动时自动起内置调度器（max_concurrent=1）
```

### 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 提交生成任务 |
| GET | `/api/tasks` | 列出任务（`?status=queued&limit=20`） |
| GET | `/api/tasks/{id}` | 任务详情（progress/stage/output/error） |
| DELETE | `/api/tasks/{id}` | 取消（仅 queued） |
| GET | `/api/health` | 健康检查 + 队列统计 |
| POST | `/api/scout` | 选题（爬热点→LLM 概念候选→入库） |
| GET | `/api/scout/{batch_id}` | 查询选题批次 |
| POST | `/api/clone_voice` | 音色克隆（爬人声→CosyVoice，异步） |
| GET | `/api/clone_voice/{job_id}` | 查询克隆任务 |
| GET | `/api/voices` | 已注册自定义音色 |
| GET | `/api/dashboard` | HTML 仪表盘 |

### 提交任务

```bash
curl -X POST http://127.0.0.1:8894/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"concept":"深海探险","character":"蓝色水母","character_mode":"flux"}'

# → {"task_id":"20260912_200827_7138","status":"queued"}
```

body 字段（全部可选除 concept）：`character` / `character_mode`（auto/3dgs/mesh/flux）/ `voice` / `duration` / `slowmo` / `lut` / `bgm` / `no_rife` / `no_color` / `no_bgm`

### 轮询任务

```bash
curl http://127.0.0.1:8894/api/tasks/20260912_200827_7138
# → {"status":"running","stage":"generating","progress":0.6,...}
# 完成后 output 字段指向 final.mp4
```

### 选题

```bash
curl -X POST http://127.0.0.1:8894/api/scout \
  -H 'Content-Type: application/json' \
  -d '{"top_per_source":15,"account_type":"萌宠","n_candidates":5}'

# → {"batch_id":"20260913_101530","topics_count":45,"candidates":[...]}
# 结果同时入库 scout_batches 表（dashboard"最近选题"板块数据源）
```

### 音色克隆（异步）

```bash
# 提交（后台下载+VAD+转写约 1-3 分钟）
curl -X POST http://127.0.0.1:8894/api/clone_voice \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"纪录片解说","name":"doc1","desc":"纪录片-男声"}'
# → {"job_id":"voice_20260913_110434","status":"running"}

# 轮询
curl http://127.0.0.1:8894/api/clone_voice/voice_20260913_110434
# → {"status":"completed","voice":"cosy-doc1","sample":".../voice_samples/...wav"}
```

body 字段：`keyword`（搜索词，默认"新闻播报"）/ `name`（音色名，空则自动时间戳）/ `index`（用第 N 个搜索候选）/ `desc`（描述）/ `reload_tts`（克隆后热加载 TTS，默认 true）

### 数据落盘

| 数据 | 位置 |
|------|------|
| 任务队列 | `output/tasks.sqlite`（tasks 表） |
| 选题批次 | `output/tasks.sqlite`（scout_batches 表） |
| 每任务产出 | `output/{task_id}/final.mp4` + `meta.json` |
| 调度日志 | `output/{task_id}/scheduler.log` |
| 克隆试听样本 | `voice_samples/cosy-{name}__{desc}.wav` |

---

## M2 — 仪表盘

```bash
# 离线生成（读 tasks.sqlite + 任务目录缩略图）
python core/scheduler.py dashboard -o output/dashboard.html

# API 实时生成
curl http://127.0.0.1:8894/api/dashboard -o dashboard.html
```

自包含 HTML（base64 缩略图，无外部依赖），含队列统计 / 最近任务 / 选题批次。浏览器直接打开。

---

## M3 — 声音克隆（utils/voice_clone.py）

### [1] 搜索人声源

```bash
# B站搜索（返回候选列表：标题/UP主/时长/播放量/bvid）
python utils/voice_clone.py search --keyword 新闻播报 --top 10

# 自定义最短时长（默认 180s，太短切不出干净 5-10s 段）
python utils/voice_clone.py search --keyword 电影解说 --top 8 --min-dur 300
```

### [2] 完整克隆流水线

```bash
# 搜索 → 下载 → VAD 切段 → 转写 → 注册 → 测试合成，一条龙
python utils/voice_clone.py clone --keyword 新闻播报 --name xinwen1 --desc "新闻播音-男声"

# 用第 N 个搜索候选（0 = 播放量最高；下载/切段失败自动尝试下一个，最多 3 个）
python utils/voice_clone.py clone --keyword 电影解说 --name jieshuo1 --index 1

# 自定义 VAD 扫描时长（默认扫前 300s）
python utils/voice_clone.py clone --keyword 纪录片 --name doc1 --scan-seconds 600
```

### [3] 测试已注册音色

```bash
# 默认测试句（样本存 voice_samples/）
python utils/voice_clone.py test --name xinwen1

# 自定义文本
python utils/voice_clone.py test --name xinwen1 --text "自定义测试文本"
```

### [4] 列出已注册音色

```bash
python utils/voice_clone.py list
# → cosy-xinwen1 新闻播音-男声 | 源: 9月12号一觉醒来...
```

### [5] 热加载（免重启 TTS server）

```bash
# voices/ 目录手动放入 {name}/prompt.wav + meta.json 后，热加载注册
curl -X POST http://127.0.0.1:9880/voices/reload
```

### 克隆产出

| 文件 | 说明 |
|------|------|
| `voices/{name}/prompt.wav` | 24kHz mono 16bit 人声段（5-10s） |
| `voices/{name}/meta.json` | prompt_text / desc / 来源 URL / 时间戳 |
| `voice_samples/cosy-{name}__{desc}.wav` | 克隆合成测试样本（试听） |

### 使用克隆音色

```bash
# 注册后直接按名调用（TTS server 走注册表）
python utils/tts.py "旁白文本" -v cosy-xinwen1 -o output/clips/narration.wav

# 生成任务指定音色
python core/scheduler.py submit "概念" --voice cosy-xinwen1
```

### 手动放音色（不爬取）

```bash
mkdir -p voices/mystyle
cp my_voice.wav voices/mystyle/prompt.wav          # 需 24kHz mono 16bit，5-10s
cat > voices/mystyle/meta.json << 'EOF'
{"prompt_text": "音频里说的原话（STT 转写）", "desc": "我的音色"}
EOF
curl -X POST http://127.0.0.1:9880/voices/reload    # 热加载
```

> `prompt_text` 与 prompt.wav 内容必须一致（CosyVoice zero-shot 要求），可用 `python utils/stt.py voices/mystyle/prompt.wav` 转写获得。

---

## 典型工作流：热点 → 成片（全链路）

```bash
# 1. 选题（爬热点 → LLM 概念候选）
curl -X POST http://127.0.0.1:8894/api/scout \
  -d '{"account_type":"萌宠","n_candidates":5}' -H 'Content-Type: application/json'
# 从 candidates 里挑 predicted_score 最高的 concept

# 2. （可选）克隆音色
python utils/voice_clone.py clone --keyword 新闻播报 --name v2 --desc "新闻播音"

# 3. 提交生成任务
curl -X POST http://127.0.0.1:8894/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"concept":"一只橘猫用擦丝器把土豆擦成旋风薯塔","character":"橘猫","character_mode":"flux","voice":"cosy-xinwen1"}'

# 4. 轮询直到完成
curl http://127.0.0.1:8894/api/tasks/{task_id}

# 5. 看成片
# output/{task_id}/final.mp4
```

---

## 服务依赖（v4）

| 工具 | 依赖服务 | 端口 |
|------|---------|------|
| crawler / scout | 无（纯 HTTP 爬取 + USTC LLM API） | — |
| api_server（生成任务） | Wan 8189 + FLUX 8192（flux 模式）+ TTS 9880 | — |
| api_server 本身 | 无外部依赖（SQLite） | 8894 |
| voice_clone（下载/切段/转写） | yt-dlp + faster-whisper（内嵌 GPU）+ ffmpeg | — |
| voice_clone（测试合成） | TTS server | 9880 |
| TTS server | CosyVoice2（GPU2）+ edge-tts（在线） | 9880 |
