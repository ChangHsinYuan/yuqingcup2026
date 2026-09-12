# Topic Scouting Skill

## 何时使用

当需要从热点话题中生成视频概念候选时使用此 skill。

## 输入

- `hot_topics`: 爬虫抓取的热点列表 [{title, source, heat, snippet}]
- `account_type`: 账号定位（如"影视解说"/"萌宠"/"情感"/"科技科普"）
- `n_candidates`: 生成候选数（默认 5）

## 输出

按 predicted_score 降序排列的概念候选列表：

```json
[
  {
    "concept": "一句话中文概念",
    "angle": "创意角度说明",
    "reason": "选题理由",
    "predicted_score": 8,
    "source_topic": "关联热点标题"
  }
]
```

## 选题原则

1. **画面感优先**：概念必须能直接想象出画面，适合 AI 视频生成
2. **角色明确**：有清晰的主角和动作
3. **热点关联但不照搬**：热点是灵感，不是直接复述
4. **角度创新**：拟人化/反转/科普/情感共鸣/跨界混搭
5. **传播潜力**：有话题性、易引发讨论

## 使用方法

```python
from utils.crawler import Crawler
from utils.llm import LLMClient

crawler = Crawler()
topics = crawler.fetch_hot_topics(top_per_source=20)

llm = LLMClient()
candidates = llm.scout_topics(topics, account_type='影视解说', n_candidates=5)

# 选 top-1 进入生成队列
best = candidates[0]
concept = best['concept']
```
