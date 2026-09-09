#!/usr/bin/env python3
"""faster-whisper STT 兜底 — 当 TTS 时间戳不够精确时，用 STT 重新对齐字幕

用法:
  from utils.stt import transcribe_audio
  result = transcribe_audio('shot_1.wav', language='zh')
  # result = [{'text': '...', 'start': 0.0, 'end': 1.5}, ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')

_MODEL_CACHE = {}


def _get_model(model_path: str, device: str = 'cuda', compute_type: str = 'int8_float16'):
    """懒加载 Whisper 模型（缓存实例避免重复加载）"""
    key = (model_path, device, compute_type)
    if key not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        _MODEL_CACHE[key] = WhisperModel(model_path, device=device, compute_type=compute_type)
    return _MODEL_CACHE[key]


def _resolve_model_path(config: dict = None) -> str:
    """从配置或默认路径解析模型路径"""
    if config is None:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)

    stt_config = config.get('stt', {})
    model_path = stt_config.get('model_path')

    if model_path and os.path.isdir(model_path):
        return model_path

    # 默认路径
    default = '/mnt/dataset/zxy/hf_cache/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
    if os.path.isdir(default):
        return default

    # 回退到模型名（自动下载）
    return stt_config.get('model_name', 'large-v3-turbo')


import json


def transcribe_audio(audio_path: str, language: str = 'zh',
                     config: dict = None, device: str = 'cuda',
                     compute_type: str = 'int8_float16',
                     word_timestamps: bool = False) -> list:
    """用 faster-whisper 转写音频，返回带时间戳的片段列表。

    Args:
        audio_path: 音频文件路径
        language: 语言代码 ('zh'=中文, 'en'=英文, None=自动检测)
        config: 配置 dict（可选）
        device: 'cuda' 或 'cpu'
        compute_type: 'int8_float16' / 'float16' / 'int8'
        word_timestamps: 是否返回词级时间戳

    Returns:
        [{'text': '...', 'start': 0.0, 'end': 1.5, 'words': [...]?}, ...]
    """
    model_path = _resolve_model_path(config)
    model = _get_model(model_path, device, compute_type)

    segments, info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=word_timestamps,
        vad_filter=True,
    )

    results = []
    for seg in segments:
        item = {
            'text': seg.text.strip(),
            'start': round(seg.start, 3),
            'end': round(seg.end, 3),
            'duration': round(seg.end - seg.start, 3),
        }
        if word_timestamps and seg.words:
            item['words'] = [
                {'word': w.word, 'start': round(w.start, 3), 'end': round(w.end, 3),
                 'probability': round(w.probability, 3)}
                for w in seg.words
            ]
        results.append(item)

    return results


def transcribe_to_srt(audio_path: str, srt_path: str, language: str = 'zh',
                      config: dict = None, offset: float = 0.0) -> str:
    """转写音频并直接生成 SRT 字幕文件。

    Args:
        audio_path: 音频文件路径
        srt_path: SRT 输出路径
        language: 语言代码
        config: 配置 dict
        offset: 时间偏移（秒）

    Returns:
        SRT 文件路径
    """
    from utils.ffmpeg_tools import generate_srt
    segments = transcribe_audio(audio_path, language=language, config=config)
    return generate_srt(segments, srt_path, offset=offset)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='faster-whisper STT')
    parser.add_argument('audio', help='Audio file path')
    parser.add_argument('-o', '--output', default=None, help='Output SRT file (optional)')
    parser.add_argument('-l', '--language', default='zh', help='Language code')
    parser.add_argument('--word-timestamps', action='store_true', help='Word-level timestamps')
    args = parser.parse_args()

    if args.output:
        srt = transcribe_to_srt(args.audio, args.output, language=args.language)
        print(f'SRT: {srt}')
    else:
        results = transcribe_audio(args.audio, language=args.language,
                                   word_timestamps=args.word_timestamps)
        for r in results:
            print(f'  [{r["start"]:.2f}-{r["end"]:.2f}] {r["text"]}')
            if 'words' in r:
                for w in r['words']:
                    print(f'    {w["word"]} ({w["start"]:.2f}-{w["end"]:.2f}, p={w["probability"]})')
