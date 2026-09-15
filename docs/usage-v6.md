# Vidance v6 使用指南 — prompt 多轮核实

> v6 四个里程碑（M1 完整度评估 / M2 对话状态机 / M3 CLI ask / M4 API 三端点）的使用方法。
>
> 主入口使用见 [usage.md](./usage.md)，设计见 [v6-design.md](./v6-design.md)

## 总览

| 里程碑 | 工具 | 一句话 |
|--------|------|--------|
| M1 | `utils/llm.py assess_completeness()` | 概念 6 维完整度评估（主体/场景/情绪/风格/时长/镜头感） |
| M2 | `utils/dialog.py` | 多轮对话状态机（OPEN/COLLECTING/COMPLETE/LOCKED，≤3 轮，SQLite） |
| M3 | `core/vidance.py ask` | 交互式多轮追问 → 增强概念 |
| M4 | `core/api_server.py /api/dialog/*` | REST 三端点（供 v7 前端用） |

**6 维规则**：6 个维度（subject 主体 / scene 场景 / emotion 情绪 / style 视觉风格 / duration 时长 / shot 镜头感）**全部齐才 COMPLETE**——prompt 靠多轮追问逐步变长变详细，style/duration/shot 也纳入追问。subject/scene/emotion 需明确描述（仅名词隐含推测不算）。3 轮未齐强制 LOCKED 放行。不做自动补全（宁可多问）。

---

## M3 — CLI 交互式（core/vidance.py ask）

```bash
cd /mnt/disk_sdb/zxy/vidance

# 交互多轮追问（终端逐条回答，"随便"/"q" 跳过/退出）
python core/vidance.py ask "深海探险"

# 追问完成后把增强概念存文件（可直接喂 auto/fast）
python core/vidance.py ask "深海探险" --out input/concept.txt
```

流程：`start` 评估 → 逐条追问（input 循环）→ 每轮重新评估 → COMPLETE/LOCKED 后输出增强概念。

管道答案（非交互）也支持：

```bash
printf "发光的蓝鲸\n深海幽暗\n神秘宁静\n" | python core/vidance.py ask "深海探险"
```

---

## M4 — dialog API（:8894，v7 前端底层）

前置：API server 运行中（`python core/api_server.py --port 8894`）。

```bash
# 1) 启动对话：评估概念，返回缺失维度 + 追问列表
curl -X POST http://127.0.0.1:8894/api/dialog/start \
  -H 'Content-Type: application/json' -d '{"concept":"深夜灯塔"}'
# → {"dialog_id":"dlg_...","status":"OPEN","dimensions":{...},"missing":["情绪基调"],
#    "questions":["..."],"concept":"深夜灯塔"}
# （概念信息已齐时直接返回 status=COMPLETE）

# 2) 回复追问：body 字段是 text；每轮返回重新评估结果 + 增强后的 concept
curl -X POST http://127.0.0.1:8894/api/dialog/dlg_xxx/reply \
  -H 'Content-Type: application/json' \
  -d '{"text":"海边悬崖上的老灯塔，暴风雨夜"}'
# → {"status":"OPEN|COLLECTING|COMPLETE|LOCKED","turns":[...],"missing":[...],
#    "questions":[...],"concept":"深夜灯塔；海边悬崖上的老灯塔，暴风雨夜"}

# 3) 查询对话当前状态
curl http://127.0.0.1:8894/api/dialog/dlg_xxx
# → {id, concept, turns, dimensions, missing, status, created_at, updated_at}
```

状态机：

| 状态 | 含义 |
|------|------|
| OPEN | 刚启动/首轮，必填维度有缺，待追问 |
| COLLECTING | 第 2 轮起仍有缺，收集中 |
| COMPLETE | 6 维全部确认齐，可进流水线 |
| LOCKED | 3 轮上限到，强制放行（用已收集信息） |

增强概念 `concept` = 原概念 + 各轮回答用 `；` 拼接（"随便"/"都可以"类回答跳过），可直接喂 `vidance.py auto/fast` 的 concept 参数。

---

## Python API（utils/dialog.py）

```python
from utils.dialog import DialogManager

dm = DialogManager()                      # 默认 SQLite output/tasks.sqlite
s = dm.start('深海探险')                   # → {dialog_id, status, missing, questions, concept}
s = dm.reply(s['dialog_id'], '发光的蓝鲸')  # → 更新状态 + 增强概念
s['status'], s['missing'], s['questions']
concept = dm.locked_concept(s['dialog_id'])  # 增强概念（合并各轮回答）
dialogs = dm.list()                        # 最近 20 条对话
```

## 验证记录（2026-09-15）

- API 8894 实测：start"深海探险" → OPEN + missing[场景/环境,情绪基调] 2 问；reply"幽暗海底…神秘宁静" → **COMPLETE**，concept 正确拼装
- CLI `ask` 修复后实测：裸概念"深海探险"一轮问完 场景+情绪 2 问 → COMPLETE；完整概念（含主体/场景/情绪明确描述）零追问直接 COMPLETE
- 修复记录 ①：`cmd_ask` idx 跨轮累加 bug——每答一条就 reply 导致 idx=1 而新轮问题常只剩 1 条，`idx >= len(qs)` 直接 break，**只问到一条就退出**（用户实测"只有一轮"）。已重构为"每轮把当前问题逐条问完 → `；`合并成一条 reply → 一次重评"，轮数 ≤3
- 修复记录 ②：llm.py `assess_completeness` 与 dialog.py 状态判定原把选填维度（style/duration/shot）也当必填，导致 missing=[] 仍卡 COLLECTING；已改为只以必填维度判 COMPLETE
- 修复记录 ④：停止条件过松——必填 3 维齐即 COMPLETE，答 2 问就停、prompt 长不起来（用户反馈）。改为**全 6 维齐才 COMPLETE**：style/duration/shot 也纳入追问，裸概念"深海探险"经 2 轮 6 问长到 7 段增强概念；信息全的概念仍零追问直接放行
- 修复记录 ③：评估口径收紧（对齐"宁可多问"）——subject/scene/emotion 需**明确描述**才算已具备，仅从名词隐含推测（"灯塔"→海边）不算；temperature 0.3→0.1 减少同一概念两次评估不一致
