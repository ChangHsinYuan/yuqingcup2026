#!/usr/bin/env python3
"""CosyVoice2 TTS FastAPI 服务（运行在 cosyvoice conda env, GPU2:9880）

启动:
  conda activate cosyvoice
  CUDA_VISIBLE_DEVICES=2 python utils/tts_server.py

接口:
  POST /tts   — 合成语音，返回 base64 audio + 句子级时间戳
  GET  /voices — 列出可用音色
  GET  /health — 健康检查
"""
import sys
import os
import re
import io
import time
import base64
import logging

# 确保 cosyvoice 包可导入
COSYVOICE_REPO = '/mnt/disk_sdb/zxy/CosyVoice'
sys.path.insert(0, COSYVOICE_REPO)
sys.path.insert(0, os.path.join(COSYVOICE_REPO, 'third_party/Matcha-TTS'))
os.chdir(COSYVOICE_REPO)

import torch
import torchaudio
import soundfile as sf
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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

PRESET_VOICES = {
    'default': {
        'prompt_wav': '/mnt/disk_sdb/zxy/CosyVoice/asset/zero_shot_prompt.wav',
        'prompt_text': '希望你以后能够做的比我还好呦。',
    },
    'cross_lingual': {
        'prompt_wav': '/mnt/disk_sdb/zxy/CosyVoice/asset/cross_lingual_prompt.wav',
        'prompt_text': '',
    },
}

# ── 全局模型实例 ──
cosyvoice = None
sample_rate = None


def load_model():
    global cosyvoice, sample_rate
    t0 = time.time()
    logger.info(f'loading CosyVoice2 from {MODEL_DIR}, fp16={FP16}')
    cosyvoice = AutoModel(model_dir=MODEL_DIR, load_jit=False, load_trt=False, fp16=FP16)
    sample_rate = cosyvoice.sample_rate
    logger.info(f'model loaded in {time.time()-t0:.1f}s, sample_rate={sample_rate}')


def split_sentences(text: str) -> list:
    """按中文句号/问号/感叹号/分号分句，保留标点"""
    text = text.strip()
    if not text:
        return []
    parts = re.split(r'(?<=[。！？!?；;])', text)
    sentences = []
    for p in parts:
        p = p.strip()
        if p:
            sentences.append(p)
    if not sentences:
        sentences = [text]
    return sentences


def synthesize(text: str, voice: str = 'default', speed: float = 1.0,
               prompt_wav: str = None, prompt_text: str = None) -> dict:
    """合成语音，返回 {audio_bytes, duration, timestamps}"""
    if cosyvoice is None:
        raise RuntimeError('model not loaded')

    # 选择音色
    if prompt_wav is None:
        voice_cfg = PRESET_VOICES.get(voice, PRESET_VOICES['default'])
        prompt_wav = voice_cfg['prompt_wav']
        prompt_text = prompt_text if prompt_text is not None else voice_cfg['prompt_text']

    if not os.path.isfile(prompt_wav):
        raise FileNotFoundError(f'prompt_wav not found: {prompt_wav}')

    sentences = split_sentences(text)
    logger.info(f'tts: {len(sentences)} sentences, voice={voice}, speed={speed}')

    audio_chunks = []
    timestamps = []
    current_time = 0.0

    for idx, sent in enumerate(sentences):
        for output in cosyvoice.inference_zero_shot(
            sent, prompt_text, prompt_wav, stream=False, speed=speed
        ):
            audio = output['tts_speech']
            duration = audio.shape[1] / sample_rate
            timestamps.append({
                'idx': idx + 1,
                'text': sent,
                'start': round(current_time, 3),
                'end': round(current_time + duration, 3),
                'duration': round(duration, 3),
            })
            audio_chunks.append(audio)
            current_time += duration

    if not audio_chunks:
        raise RuntimeError('no audio generated')

    full_audio = torch.cat(audio_chunks, dim=1)
    audio_np = full_audio[0].cpu().numpy()
    total_duration = current_time

    # 编码为 WAV bytes
    buf = io.BytesIO()
    sf.write(buf, audio_np, sample_rate, format='WAV')
    audio_bytes = buf.getvalue()

    logger.info(f'tts done: {total_duration:.2f}s audio, {len(timestamps)} segments')
    return {
        'audio': base64.b64encode(audio_bytes).decode('ascii'),
        'audio_format': 'wav',
        'sample_rate': sample_rate,
        'duration': round(total_duration, 3),
        'timestamps': timestamps,
    }


# ── FastAPI ──
app = FastAPI(title='Vidance TTS', description='CosyVoice2 TTS Service')


class TTSRequest(BaseModel):
    text: str
    voice: str = 'default'
    speed: float = 1.0
    prompt_wav: Optional[str] = None
    prompt_text: Optional[str] = None


@app.on_event('startup')
def startup():
    load_model()


@app.get('/health')
def health():
    return {
        'status': 'ok' if cosyvoice is not None else 'loading',
        'model': 'CosyVoice2-0.5B',
        'sample_rate': sample_rate,
        'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu',
        'voices': list(PRESET_VOICES.keys()),
    }


@app.get('/voices')
def voices():
    return {'voices': PRESET_VOICES}


@app.post('/tts')
def tts(req: TTSRequest):
    try:
        t0 = time.time()
        result = synthesize(
            text=req.text,
            voice=req.voice,
            speed=req.speed,
            prompt_wav=req.prompt_wav,
            prompt_text=req.prompt_text,
        )
        result['elapsed'] = round(time.time() - t0, 3)
        return result
    except Exception as e:
        logger.error(f'tts error: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level='info')
