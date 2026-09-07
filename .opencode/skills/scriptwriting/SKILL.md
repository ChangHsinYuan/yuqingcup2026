---
name: scriptwriting
description: "Use when generating a video script from a concept. 分镜规范：镜头数/时长/JSON格式/旁白字数/风格描述。Trigger when writing scripts for Vidance video generation."
---

# Scriptwriting Skill — 分镜编剧规范

## 分镜脚本 JSON Schema

```json
{
  "title": "标题",
  "concept": "原始概念",
  "style": "英文风格描述",
  "shots": [
    {
      "id": 1,
      "scene_desc": "中文场景描述",
      "narration": "中文旁白文本",
      "duration": 5,
      "camera": "镜头描述"
    }
  ]
}
```

## 规则

### 镜头数与时长
- 2-5 个镜头
- 每镜 3-5 秒（Wan 单次上限 121 帧≈5s@24fps）
- 总时长 10-25 秒

### 旁白字数
- 中文约 4 字/秒
- narration 字数 = duration × 4（±20%）
- 5 秒 ≈ 20 字，3 秒 ≈ 12 字

### scene_desc 要求
- 具体描述：主体 + 动作 + 环境 + 光线
- 避免抽象概念，要可视化
- 例如："广角镜头，月球表面坑洼不平，一只黑白猫站在环形山边缘，抬头望向地球" ✓
- 例如："一只猫在月球上" ✗（太简略）

### narration 要求
- 与 scene_desc 内容呼应
- 简洁有节奏感
- 口语化，适合配音
- 每镜独立成句（以句号结尾）

### style 要求
- 英文风格修饰词
- 常用：cinematic, 35mm film grain, warm sunset lighting, cool blue moonlight, dreamy, surreal, film noir, vibrant colors, soft focus
- 2-4 个修饰词组合

### camera 要求
- 镜头类型：wide shot, medium shot, close-up, extreme close-up
- 运镜：static camera, slow tracking, pan left/right, zoom in/out, crane shot
- 组合示例："wide shot, static camera", "close-up, slow tracking follow"

## 示例

概念："一只猫在月球上跳舞"

```json
{
  "title": "月舞喵",
  "concept": "一只猫在月球上跳舞",
  "style": "surreal dreamy, cool blue moonlight, cinematic 35mm film grain",
  "shots": [
    {
      "id": 1,
      "scene_desc": "广角镜头，月球表面坑洼不平，银白色尘土闪烁。一只黑白猫站在环形山边缘，抬头望向地球。",
      "narration": "在寂静的月球上，一只猫找到了它的舞台。",
      "duration": 5,
      "camera": "wide shot, static camera"
    },
    {
      "id": 2,
      "scene_desc": "中景，猫旋转跳跃，前爪轻盈点地，尘土在脚下扬起如烟雾。背景是地球和繁星。",
      "narration": "它旋转，跳跃，与星辰共舞。",
      "duration": 5,
      "camera": "medium shot, slow tracking follow"
    },
    {
      "id": 3,
      "scene_desc": "特写，猫爪落在尘土上留下脚印。猫回头看向镜头，瞳孔在月光下闪烁，转身跑向远处。",
      "narration": "在这片无人的寂静中，它跳出了自己的宇宙。",
      "duration": 4,
      "camera": "close-up, then pull back to wide shot"
    }
  ]
}
```
