#!/usr/bin/env python3
"""Vidance TTS FastAPI 服务 — 双引擎（CosyVoice2 + edge-tts）

运行在 cosyvoice conda env, GPU2:9880

启动:
  conda activate cosyvoice
  CUDA_VISIBLE_DEVICES=2 TTS_FP16=1 python utils/tts_server.py

引擎:
  - CosyVoice2 (GPU, 本地, zero-shot 克隆)  → voice 以 "cosy-" 开头或为预置名
  - edge-tts   (CPU, 在线, 微软神经语音)      → voice 以 "edge-" 开头

接口:
  POST /tts     — 合成语音，返回 base64 wav + 句子级时间戳
  GET  /voices  — 列出所有可用音色（含 edge + cosy + 自定义）
  GET  /health  — 健康检查
"""
import sys
import os
import re
import io
import time
import json
import base64
import asyncio
import logging
import subprocess

# ── CosyVoice 路径 ──
COSYVOICE_REPO = '/mnt/disk_sdb/zxy/CosyVoice'
sys.path.insert(0, COSYVOICE_REPO)
sys.path.insert(0, os.path.join(COSYVOICE_REPO, 'third_party/Matcha-TTS'))
os.chdir(COSYVOICE_REPO)

import torch
import torchaudio
import soundfile as sf
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from cosyvoice.cli.cosyvoice import AutoModel

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ── 配置 ──
MODEL_DIR = os.environ.get('COSYVOICE_MODEL_DIR', '/mnt/dataset/zxy/CosyVoice2-0.5B')
HOST = os.environ.get('TTS_HOST', '0.0.0.0')
PORT = int(os.environ.get('TTS_PORT', '9880'))
FP16 = os.environ.get('TTS_FP16', '1') == '1'
CUSTOM_VOICE_DIR = os.environ.get('TTS_VOICE_DIR', '/mnt/disk_sdb/zxy/vidance/voices')
FFMPEG_BIN = os.environ.get('FFMPEG_BIN', 'ffmpeg')

# ── CosyVoice 预置音色 ──
COSY_PRESET_VOICES = {
    'cosy-default': {
        'prompt_wav': '/mnt/disk_sdb/zxy/CosyVoice/asset/zero_shot_prompt.wav',
        'prompt_text': '希望你以后能够做的比我还好呦。',
        'desc': 'CosyVoice默认（偏机械）',
    },
    'cosy-cross': {
        'prompt_wav': '/mnt/disk_sdb/zxy/CosyVoice/asset/cross_lingual_prompt.wav',
        'prompt_text': '',
        'desc': 'CosyVoice跨语言',
    },
}

# ── edge-tts 音色预设 ──
# (voice_id, desc, rate, pitch)
EDGE_VOICE_PRESETS = {
    'edge-xiaoxiao':  ('zh-CN-XiaoxiaoNeural',           '晓晓-女声自然',     '+0%',  '+0Hz'),
    'edge-xiaoyi':    ('zh-CN-XiaoyiNeural',             '晓伊-女声活泼',     '+0%',  '+0Hz'),
    'edge-yunjian':   ('zh-CN-YunjianNeural',            '云健-男声沉稳',     '+0%',  '+0Hz'),
    'edge-yunxi':     ('zh-CN-YunxiNeural',              '云希-男声年轻',     '+0%',  '+0Hz'),
    'edge-yunxia':    ('zh-CN-YunxiaNeural',             '云夏-男声少年',     '+0%',  '+0Hz'),
    'edge-yunyang':   ('zh-CN-YunyangNeural',            '云扬-男声播音',     '+0%',  '+0Hz'),
    'edge-xiaobei':   ('zh-CN-liaoning-XiaobeiNeural',   '小贝-东北女声',     '+0%',  '+0Hz'),
    'edge-xiaoni':    ('zh-CN-shaanxi-XiaoniNeural',     '小妮-陕西女声',     '+0%',  '+0Hz'),
    # ── meme / 风格化变体 ──
    'edge-moe':       ('zh-CN-XiaoyiNeural',             '萌系高音-哈基米风', '+15%', '+80Hz'),
    'edge-family':    ('zh-CN-XiaoxiaoNeural',           '夸张解说-家人们风', '+25%', '+30Hz'),
    'edge-slow':      ('zh-CN-YunjianNeural',            '慢速抒情旁白',     '-15%', '+0Hz'),
    'edge-teen':      ('zh-CN-YunxiaNeural',             '少年音加速',       '+20%', '+20Hz'),
    'edge-dongbei':   ('zh-CN-liaoning-XiaobeiNeural',   '东北风加速',       '+15%', '+0Hz'),
    'edge-deep':      ('zh-CN-YunjianNeural',            '低沉男声',         '-5%',  '-30Hz'),
    'edge-fast-xiaoyi': ('zh-CN-XiaoyiNeural',           '晓伊-加速活泼',    '+25%', '+0Hz'),
    'edge-fast-xiaoxiao': ('zh-CN-XiaoxiaoNeural',       '晓晓-加速高音',    '+30%', '+50Hz'),
}

# ── 全局模型 ──
cosyvoice = None
cosy_sample_rate = None


def load_model():
    global cosyvoice, cosy_sample_rate
    t0 = time.time()
    logger.info(f'loading CosyVoice2 from {MODEL_DIR}, fp16={FP16}')
    cosyvoice = AutoModel(model_dir=MODEL_DIR, load_jit=False, load_trt=False, fp16=FP16)
    cosy_sample_rate = cosyvoice.sample_rate
    logger.info(f'cosyvoice loaded in {time.time()-t0:.1f}s, sr={cosy_sample_rate}')


def scan_custom_voices():
    """扫描 voices/ 目录，注册自定义 CosyVoice 音色"""
    custom = {}
    if not os.path.isdir(CUSTOM_VOICE_DIR):
        return custom
    for name in sorted(os.listdir(CUSTOM_VOICE_DIR)):
        vdir = os.path.join(CUSTOM_VOICE_DIR, name)
        if not os.path.isdir(vdir):
            continue
        prompt_wav = os.path.join(vdir, 'prompt.wav')
        meta_file = os.path.join(vdir, 'meta.json')
        if not os.path.isfile(prompt_wav):
            continue
        prompt_text = ''
        desc = name
        if os.path.isfile(meta_file):
            try:
                meta = json.load(open(meta_file, encoding='utf-8'))
                prompt_text = meta.get('prompt_text', '')
                desc = meta.get('desc', name)
            except Exception:
                pass
        voice_key = f'cosy-{name}'
        custom[voice_key] = {
            'prompt_wav': prompt_wav,
            'prompt_text': prompt_text,
            'desc': desc,
        }
        logger.info(f'custom voice: {voice_key} → {desc}')
    return custom


def split_sentences(text: str) -> list:
    """按中文标点分句"""
    text = text.strip()
    if not text:
        return []
    parts = re.split(r'(?<=[。！？!?；;\n])', text)
    sentences = [p.strip() for p in parts if p.strip()]
    return sentences or [text]


# ── CosyVoice 合成 ──
def synthesize_cosyvoice(text, voice, speed, prompt_wav, prompt_text):
    if cosyvoice is None:
        raise RuntimeError('cosyvoice model not loaded')
    all_voices = {**COSY_PRESET_VOICES, **CUSTOM_VOICES}
    if prompt_wav is None:
        cfg = all_voices.get(voice, all_voices.get('cosy-default'))
        prompt_wav = cfg['prompt_wav']
        prompt_text = prompt_text if prompt_text is not None else cfg['prompt_text']
    if not os.path.isfile(prompt_wav):
        raise FileNotFoundError(f'prompt_wav not found: {prompt_wav}')

    sentences = split_sentences(text)
    audio_chunks = []
    timestamps = []
    current = 0.0
    for idx, sent in enumerate(sentences):
        for output in cosyvoice.inference_zero_shot(
            sent, prompt_text, prompt_wav, stream=False, speed=speed
        ):
            audio = output['tts_speech']
            dur = audio.shape[1] / cosy_sample_rate
            timestamps.append({
                'idx': idx + 1, 'text': sent,
                'start': round(current, 3), 'end': round(current + dur, 3),
                'duration': round(dur, 3),
            })
            audio_chunks.append(audio)
            current += dur
    if not audio_chunks:
        raise RuntimeError('no audio generated')
    full = torch.cat(audio_chunks, dim=1)
    audio_np = full[0].cpu().numpy()
    buf = io.BytesIO()
    sf.write(buf, audio_np, cosy_sample_rate, format='WAV')
    return {
        'audio': base64.b64encode(buf.getvalue()).decode('ascii'),
        'audio_format': 'wav', 'sample_rate': cosy_sample_rate,
        'duration': round(current, 3), 'timestamps': timestamps,
        'engine': 'cosyvoice', 'voice': voice,
    }


# ── edge-tts 合成 ──
async def synthesize_edge(text, voice, speed):
    import edge_tts
    cfg = EDGE_VOICE_PRESETS.get(voice)
    if cfg is None:
        raise ValueError(f'unknown edge voice: {voice}')
    edge_vid, _desc, rate, pitch = cfg
    # speed: CosyVoice 用乘数 (1.0=正常), edge 用百分比
    edge_rate = f'{int((speed - 1) * 100):+d}%' if speed != 1.0 else rate
    # 合并预设 rate 和 speed 参数（叠加）
    if speed != 1.0 and rate != '+0%':
        base = int(rate.strip('%+'))
        extra = int((speed - 1) * 100)
        edge_rate = f'{base + extra:+d}%'

    sentences = split_sentences(text)
    audio_chunks = []
    timestamps = []
    current = 0.0
    for idx, sent in enumerate(sentences):
        communicate = edge_tts.Communicate(
            sent, edge_vid, rate=edge_rate, pitch=pitch, boundary='WordBoundary'
        )
        mp3_data = b''
        word_cues = []
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                mp3_data += chunk['data']
            elif chunk['type'] == 'WordBoundary':
                word_cues.append(chunk)
        if not mp3_data:
            raise RuntimeError(f'edge-tts no audio for sentence {idx+1}')
        # MP3 → WAV (24kHz mono)
        wav_bytes = mp3_to_wav(mp3_data, target_sr=24000)
        dur = len(wav_bytes) / (24000 * 2)  # 16bit mono = 2 bytes/sample
        timestamps.append({
            'idx': idx + 1, 'text': sent,
            'start': round(current, 3), 'end': round(current + dur, 3),
            'duration': round(dur, 3),
        })
        audio_chunks.append(wav_bytes)
        current += dur
    full_wav = b''.join(audio_chunks)
    return {
        'audio': base64.b64encode(full_wav).decode('ascii'),
        'audio_format': 'wav', 'sample_rate': 24000,
        'duration': round(current, 3), 'timestamps': timestamps,
        'engine': 'edge', 'voice': voice,
    }


def mp3_to_wav(mp3_bytes, target_sr=24000):
    """用 ffmpeg 将 MP3 bytes 转为 WAV bytes (16bit mono, target_sr)"""
    proc = subprocess.run(
        [FFMPEG_BIN, '-i', 'pipe:0', '-ar', str(target_sr),
         '-ac', '1', '-f', 'wav', 'pipe:1'],
        input=mp3_bytes, capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg failed: {proc.stderr[:500]}')
    return proc.stdout


# ── FastAPI ──
app = FastAPI(title='Vidance TTS', description='CosyVoice2 + edge-tts 双引擎')

CUSTOM_VOICES = {}


class TTSRequest(BaseModel):
    text: str
    voice: str = 'cosy-default'
    speed: float = 1.0
    prompt_wav: Optional[str] = None
    prompt_text: Optional[str] = None


@app.on_event('startup')
def startup():
    global CUSTOM_VOICES
    load_model()
    CUSTOM_VOICES = scan_custom_voices()


@app.get('/health')
def health():
    return {
        'status': 'ok' if cosyvoice is not None else 'loading',
        'engines': ['cosyvoice', 'edge-tts'],
        'cosyvoice_model': 'CosyVoice2-0.5B',
        'sample_rate': cosy_sample_rate,
        'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu',
        'voice_count': len(COSY_PRESET_VOICES) + len(EDGE_VOICE_PRESETS) + len(CUSTOM_VOICES),
    }


@app.get('/voices')
def list_voices():
    return {
        'cosyvoice': COSY_PRESET_VOICES,
        'edge': {k: {'voice_id': v[0], 'desc': v[1], 'rate': v[2], 'pitch': v[3]}
                 for k, v in EDGE_VOICE_PRESETS.items()},
        'custom': CUSTOM_VOICES,
    }


@app.post('/tts')
async def tts(req: TTSRequest):
    try:
        t0 = time.time()
        voice = req.voice
        if voice.startswith('edge-'):
            result = await synthesize_edge(req.text, voice, req.speed)
        else:
            # CosyVoice 是同步阻塞的，放到线程池
            result = await asyncio.to_thread(
                synthesize_cosyvoice, req.text, voice, req.speed,
                req.prompt_wav, req.prompt_text,
            )
        result['elapsed'] = round(time.time() - t0, 3)
        return result
    except Exception as e:
        logger.error(f'tts error: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level='info')
