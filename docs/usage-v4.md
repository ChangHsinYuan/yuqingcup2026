# Vidance v4 使用指南 — 营销号流水线小工具

> v4 五个里程碑（M1 爬虫选题 / M2 异步任务队列 / M3 声音克隆 / M4 FunClip 智能裁剪 / M5 素材爬取）的逐个小步骤使用方法。
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
| M4 | `utils/funclip.py` | 长素材 ASR 转写 + 字级时间戳 + LLM 语义裁剪 |
| M5 | `utils/asset_crawler.py` | 参考图搜索下载 + LLM 关键词 + BGM 免版权爬曲 |

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
| POST | `/api/clip` | FunClip 长素材语义裁剪（异步） |
| GET | `/api/clip/{job_id}` | 查询裁剪任务 |

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

### 长素材裁剪（异步）

```bash
# 提交（ASR 转写 + LLM 判定 + 裁剪，约 30s-2min）
curl -X POST http://127.0.0.1:8894/api/clip \
  -H 'Content-Type: application/json' \
  -d '{"input":"/path/long.wav","instructions":"只要讲龙的部分，去掉口误","output":"dragon.wav"}'
# → {"job_id":"clip_20260913_192833","status":"running"}

# 轮询
curl http://127.0.0.1:8894/api/clip/clip_20260913_192833
# → {"status":"completed","output":".../output/dragon.wav","segments":[[0.1,3.05],...],"keep":[true,false,...]}
```

body 字段：`input`（长素材路径，必填）/ `instructions`（保留/删除语义，必填）/ `output`（输出文件名，相对路径落 output_dir，空则源文件旁 `*_clipped`）/ `use_llm`（默认 true）

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

## M4 — FunClip 智能裁剪（utils/funclip.py）

长素材（长旁白录制/爬取的长视频）语义裁剪：FunASR 转写 + 字级时间戳 → LLM 判断每句保留/删除 → ffmpeg 裁剪拼接。

- **场景 1**：长旁白录制有口误 → 按语义去口误段
- **场景 2**：爬取长视频素材 → 按内容提取关键段

### CLI

```bash
cd /mnt/disk_sdb/zxy/vidance

# [1] 转写：显示字级时间戳（模型首次自动下载 ~1.1GB，之后走本地缓存）
python utils/funclip.py transcribe output/clips/narration.wav
# → 在无边的深蓝里光从寂静中诞生
# → 共 14 字, 首字 130ms, 末字 2965ms

# 按句显示（字级时间戳按停顿聚合成句）
python utils/funclip.py transcribe narration.wav --sentences
# → [0.13-3.05] 在无边的深蓝里光从寂静中诞生

# [2] 手动按时段裁剪拼接（秒，支持视频或纯音频，输出格式自动匹配）
python utils/funclip.py clip long.mp4 --segments "0-3,5-8" -o clipped.mp4

# [3] LLM 语义裁剪（一句话描述要保留什么，LLM 逐句判定）
python utils/funclip.py smart long.wav -i "只要讲龙的部分，去掉口误" -o dragon.wav

# 跳过 LLM（全保留，仅转写+聚合调试用）
python utils/funclip.py smart long.wav -i "..." -o out.wav --no-llm
```

### Python API

```python
from utils.funclip import FunClip

fc = FunClip()   # device='auto' 自动选空闲显存最多的 GPU

# 1. 转写 → 字级时间戳
chars = fc.transcribe('long.wav')
# → [{'char':'在','start_ms':130,'end_ms':350}, ...]

# 2. 字级聚合成句
sents = fc.to_sentences(chars)
# → [{'text':'在无边的深蓝里...','start_ms':130,'end_ms':3050}, ...]

# 3. 按时段裁剪（视频保留画面，音频仅声音）
fc.clip_segments('long.wav', [(0.1, 3.0), (5.0, 8.0)], 'clipped.wav')

# 4. LLM 语义裁剪（llm 传 utils.llm.LLMClient）
from utils.llm import LLMClient
r = fc.smart_clip('long.wav', '只要讲龙的画面，去掉口误', 'out.wav', llm=LLMClient())
# → {'segments': [(s,e),...], 'sentences': [...], 'keep': [bool,...], 'duration_ms': ...}
```

### 参数与行为

| 项 | 说明 | 默认 |
|----|------|------|
| `device` | `auto` 自动选空闲显存最多的卡（逐卡探测，满卡自动跳过）；或显式 `cuda:N` | `auto` |
| `to_sentences(gap_ms)` | 字间停顿超过该值视为句边界 | `300` |
| `smart_clip(pause_ms)` | 保留段之间吸收的静音上限（防生硬） | `400` |
| 输入格式 | 视频（保留画面 yuv420p/High）或纯音频（输出 pcm_s16le wav）自动探测 | — |

> **模型**：`iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch`（FunASR 官方带字级时间戳的组合模型，首次运行自动下载到 `~/.cache/modelscope/`）。注意必须 `return_raw=True + batch_size_s=300` 才返回字级 timestamp。
> **GPU 注意**：满卡（如 GPU0 被 H3 占满）连 CUDA context 都建不出来，`device='auto'` 会逐卡探测并跳过，不会让进程崩掉。

---

## M5 — 素材爬取（utils/asset_crawler.py）

参考图 + BGM 两类素材自动爬取（人声样本已在 M3 voice_clone.py 完成）：

- **参考图**：必应图片搜索（cn.bing.com 直连可达，原图直链 murl）→ magic bytes 校验 + md5 去重 → 任务参考图目录
- **BGM**：incompetech.com（Kevin MacLeod CC BY，直连可达）按 feel 标签搜曲 → 下载 → loudnorm I=-20 → `bgm/{mood}.wav`

### CLI

```bash
cd /mnt/disk_sdb/zxy/vidance

# [1] LLM concept → 图片搜索关键词
python utils/asset_crawler.py keywords "雪山上的日出小狐狸"
# → ['雪山之巅金色晨曦狐狸剪影', '雪峰日出霞光狐狸遥望', ...]

# [2] 按关键词搜图下载（必应，magic bytes 校验 + md5 去重）
python utils/asset_crawler.py images "深海水母" -o output/ref/ --top 5

# [3] concept → 关键词 → 图片下载（一条龙）
python utils/asset_crawler.py refs "雪山上的日出小狐狸" -o output/ref/ --top 2

# [4] BGM：只列候选 / 下载（→ bgm/{mood}.wav，select_bgm 自动识别）
python utils/asset_crawler.py bgm epic --list
python utils/asset_crawler.py bgm epic --max-dur 180
```

### Python API

```python
from utils.asset_crawler import AssetCrawler
from utils.llm import LLMClient

ac = AssetCrawler()

# 参考图
kws = ac.extract_keywords('深海里发光的水母', llm=LLMClient())
# → ['深海水母发光', ...]
items = ac.download_images('深海水母', 'output/ref/', top=5)
# → [{'path': '/abs/深海水母_1.jpg', 'url': 'http://...', 'size': 43000}, ...]
items = ac.crawl_concept_refs('概念', 'output/ref/', top=2, llm=LLMClient())  # 一条龙

# BGM
cands = ac.search_bgm('epic', max_dur=180)   # 候选列表
path = ac.crawl_bgm('epic')                  # 下载+loudnorm → bgm/epic.wav
```

### 细节与注意

| 项 | 说明 |
|----|------|
| 图片源 | 必应图片 async 接口（`cn.bing.com/images/async`，直连可达）；百度 acjson 接口需真 cookie 已弃用 |
| 图片校验 | magic bytes（JPEG/PNG/WebP/GIF/BMP）+ ≥8KB + md5 去重 |
| BGM 源 | `bgm/pieces.json`（incompetech 曲目目录缓存，1442 首）；URL 格式 `mp3-royaltyfree/{filename}`，**filename 自带 `.mp3` 后缀不可重复拼** |
| BGM 时长 | `length` 字段为 `HH:MM:SS` 格式；按"越接近 60s 越好"排序（避免 200MB 巨物 + 太短不够用） |
| BGM 响度 | loudnorm I=-20:TP=-1.5:LRA=11，与现有 bgm/*.wav 一致 |
| mood 映射 | 标准 mood（calm/uplifting/mysterious/dramatic/playful/epic/sad/tense）→ feel 搜索词，未知 mood 直接当 feel 搜 |

> **BGM 使用**：爬下来的 `bgm/{mood}.wav` 自动被 `select_bgm` 的 custom_dir 优先逻辑使用（`--bgm epic` 直接生效），无需改代码。
> **参考图用途**：custom 模式 `--ref` 输入、或人工挑选后做 I2V 条件帧。

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

## M6 — 快速营销号链路（utils/fastline.py）

图片 + 运镜动效组成视频，**跳过视频模型**，分钟级出片。由 `vidance.py fast` 提供（也可直接跑模块）。

### CLI（推荐，通过 `python core/vidance.py fast`）

```bash
# 概念 → LLM 旁白 → 出图 → KenBurns 伪动态 → TTS → crossfade → LUT/BGM/STT
python core/vidance.py fast "雪山日出小狐狸" -n 5                    # 自动爬图 + 自动 BGM/LUT
python core/vidance.py fast "概念" --images-source flux -n 5          # FLUX 逐段文生图（质量可控）
python core/vidance.py fast "概念" --images dir/ --voice edge-moe     # 用自己的图
python core/vidance.py fast "概念" --bgm epic --lut warm --stt --slowmo 2  # 指定 BGM/LUT + STT + RIFE 慢放（出 final_slow.mp4，原片保留）
python core/vidance.py fast "概念" --source-topic "热搜标题" --trend-date 2026-09-14  # 顶部角标+meta 记录热搜来源

# 一步到位（固定流程）：爬实时热点 → LLM scout 选题 → 出片
python core/vidance.py hot -n 5 --top-each 10 --account "影视解说"
python core/vidance.py hot --images-source flux           # FLUX 出图（更快，跳过爬图校验）
```

### 热点成片（hot 固定流程）

`python core/vidance.py hot` 一条命令完成：crawler 爬实时热点 → LLM scout 自动选题（最高分）→ fast 出片。成片自动带：

- **图片相关性校验**：`--images-source crawl`（默认）对每张爬图用多模态 LLM 判相关性，不相关的自动删除，不足用 FLUX 补足（防止图/旁白错位）
- **FLUX 生图质量门**（`--images-source flux`）：旁白先翻成英文 FLUX prompt（`optimize_fastline_prompt`），生成后再用多模态 LLM 审核是否贴合该段旁白，不过重生成≤2 次（防止驴唇不对马嘴）
- **热搜来源角标**：片头顶部 drawtext 叠加 `{日期} 热搜: {热点标题}`（独立于字幕，不造成字幕/声音错位），并写入 meta.json 的 `source_topic`/`trend_date` 供核对
- `--no-source` 可去掉角标；`--slowmo` 另出 RIFE 慢放版（原片保留）

`hot` 额外参数：`--top-each N`(每源抓取条数，默认10)、`--n-cand N`(scout 候选数，默认6)、`--account"xxx"`(账号定位，默认"热门资讯")。

### 参数

| 参数 | 说明 |
|------|------|
| `concept` | 热点概念 / 旁白主题（中文） |
| `--images dir/` | 图片目录（有图则用，否则按 `--images-source` 出图） |
| `--images-source` | `crawl`(必应爬图+相关性校验，默认) / `flux`(FLUX 生图，含英文 prompt 翻译+审核门) |
| `--images-count/-n` | 图片/段落数（默认 5） |
| `--source-topic` | 对应热搜标题（写进 meta + 片头角标，便于核对真实性） |
| `--trend-date` | 热搜日期（如 2026-09-14，默认今天） |
| `--no-source` | 不烧录热搜来源角标 |
| `--effects` | v5 剪辑特效: `auto`(LLM 逐段选 flash/punch/glitch/颗粒/变速/定格/甩镜/推拉) / `off`(默认) |
| `-n N` | 图片/段落数（默认 5） |
| `--voice` | TTS 音色（默认 config `edge-moe`） |
| `--motion` | 统一运镜 pan/zoom-in/zoom-out（默认 LLM 每段自选） |
| `--seconds` | 每段时长（秒，默认按旁白对齐） |
| `--bgm` | BGM mood（加 `epic`） |
| `--lut` | LUT 风格 |
| `--stt` | STT 字幕对齐（否则用估算时间轴烧字） |
| `--slowmo N` | RIFE 慢放帧倍率（需 GPU2:8189，视频`final_slow.mp4` 仅画面）；原片 `final.mp4` 恒保留 |
| `-o` | 输出路径 |

### Python API

```python
from utils.fastline import FastLine
fl = FastLine()
meta = fl.run("概念", output_path=None, images_dir=None, n=5, voice="edge-moe", speed=1.0)
print(meta["output"], meta["duration"])
```

输出：`output/{task_id}/final.mp4` + `meta.json`（segments//clips/images/elapsed）。

---

## 服务依赖（v4）

| 工具 | 依赖服务 | 端口 |
|------|---------|------|
| crawler / scout | 无（纯 HTTP 爬取 + USTC LLM API） | — |
| api_server（生成任务） | Wan 8189 + FLUX 8192（flux 模式）+ TTS 9880 | — |
| api_server 本身 | 无外部依赖（SQLite） | 8894 |
| voice_clone（下载/切段/转写） | yt-dlp + faster-whisper（内嵌 GPU）+ ffmpeg | — |
| voice_clone（测试合成） | TTS server | 9880 |
| funclip | FunASR paraformer（内嵌 GPU，自动选卡）+ ffmpeg + USTC LLM API（smart 子命令） | — |
| asset_crawler | cn.bing.com（图片）+ incompetech.com（BGM）+ USTC LLM API（keywords/refs） | — |
| fastline（快速链路） | cn.bing.com（爬图）+ TTS 9880 + ffmpeg（zoompan）；LUT/BGM/STT 复用后处理 | — |
| fastline（--slowmo） | RIFE（Wan+RIFE ComfyUI） | 8189 |
| TTS server | CosyVoice2（GPU2）+ edge-tts（在线） | 9880 |
