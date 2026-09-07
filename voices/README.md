# 自定义音色目录

将 meme/自定义音色放在这里，CosyVoice zero-shot 会自动克隆。

## 目录结构

每个音色一个子目录，包含参考音频 + 元数据：

```
voices/
├── manbo/
│   ├── prompt.wav     # 5-10s 参考音频（必须是 wav）
│   └── meta.json      # {"prompt_text": "参考音频对应的文字", "desc": "曼波风格"}
├── hakimi/
│   ├── prompt.wav
│   └── meta.json
```

## 添加方法

1. 找一段 5-10 秒的目标音色音频（如曼波/哈基米的原声）
2. 转成 wav 放到 `voices/<音色名>/prompt.wav`
3. 写 `meta.json`，`prompt_text` 是那段音频里说的话（越准越好）
4. 重启 TTS 服务，自动注册为 `cosy-<音色名>`

## 调用

```bash
# 用自定义音色
curl -X POST http://localhost:9880/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"要合成的文字","voice":"cosy-manbo"}'
```

## 注意

- 参考音频质量越高，克隆效果越好（干净人声、无背景音乐）
- `prompt_text` 必须和音频内容一致，否则音色会漂移
- 5-10s 最佳，太短音色不稳，太长推理变慢
