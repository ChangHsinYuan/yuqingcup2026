#!/usr/bin/env python3
"""声音克隆生产化 — 爬人声 → 自动切分 → CosyVoice 音色注册

流程（v4 M3）:
  [1] B站搜索目标音色风格的人声视频（新闻播报/解说/故事）
  [2] yt-dlp 下载音频（bestaudio）
  [3] faster-whisper VAD 切分 → 挑 5-10s 纯人声段（跳过片头/音乐/太安静段）
  [4] ffmpeg 转 CosyVoice 要求格式（24kHz mono 16bit wav）
  [5] STT 自动转写 prompt_text
  [6] 入库 voices/{name}/ + 注册 cosy-{name}（TTS server 启动扫描或 /voices/reload）
  [7] 质量校验：用样本克隆合成测试句 → voice_samples/ 试听

用法:
  from utils.voice_clone import VoiceCloner
  vc = VoiceCloner()
  report = vc.clone(keyword='新闻播报', name='xinwen1')

  CLI:
  python utils/voice_clone.py search --keyword 新闻播报 --top 10
  python utils/voice_clone.py clone --keyword 新闻播报 --name xinwen1
  python utils/voice_clone.py test --name xinwen1 --text "测试文本"
  python utils/voice_clone.py list
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.crawler import _random_ua, _get

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_DIR = os.path.join(BASE_DIR, 'voices')
SAMPLE_DIR = os.path.join(BASE_DIR, 'voice_samples')
TMP_DIR = '/tmp/opencode/voice_clone'
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.json')

# 测试句（质量校验：能覆盖常见音素 + 中英混合）
TEST_TEXT = '大家好，欢迎收看今天的节目。Vidance 是一个自动视频生成系统，正在测试声音克隆的效果。'


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def _parse_duration(s) -> float:
    """'42:20' / '1:02:03' / 2540 → 秒"""
    if isinstance(s, (int, float)):
        return float(s)
    parts = str(s).strip().split(':')
    try:
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    except ValueError:
        return 0.0


def _vol_detect(wav_path) -> float:
    """ffmpeg volumedetect → mean_volume dB（质量门：人声段应 > -32dB）"""
    proc = subprocess.run(
        ['ffmpeg', '-i', wav_path, '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=30,
    )
    m = re.search(r'mean_volume:\s*(-?[\d.]+)\s*dB', proc.stderr)
    return float(m.group(1)) if m else -99.0


class VoiceCloner:
    """爬人声 → CosyVoice 音色 自动化克隆"""

    def __init__(self, config=None):
        self.config = config or load_config()

    # ── [1] 搜索人声源 ──
    def search_sources(self, keyword: str, top: int = 10, min_dur: float = 180) -> list:
        """B站搜索 API（随机 buvid3 cookie 过反爬）

        Returns:
            [{bvid, title, author, duration, play, url}]  按播放量降序
        """
        url = ('https://api.bilibili.com/x/web-interface/search/type'
               f'?search_type=video&keyword={urllib.request.quote(keyword)}&page=1')
        headers = {
            'Referer': 'https://www.bilibili.com/',
            'Cookie': f'buvid3={random.getrandbits(64):016x}infoc',
        }
        resp = _get(url, headers=headers, timeout=15)
        data = resp.json()
        if data.get('code') != 0:
            raise RuntimeError(f'B站搜索错误: {data.get("message", "?")}')

        items = []
        for it in data.get('data', {}).get('result', []):
            title = re.sub(r'<[^>]+>', '', it.get('title', ''))
            dur = _parse_duration(it.get('duration'))
            if dur < min_dur:  # 太短切不出干净 5-10s 段
                continue
            items.append({
                'bvid': it.get('bvid', ''),
                'title': title,
                'author': it.get('author', ''),
                'duration': dur,
                'play': it.get('play', 0),
                'url': f"https://www.bilibili.com/video/{it.get('bvid', '')}",
            })
        items.sort(key=lambda x: x['play'], reverse=True)
        return items[:top]

    # ── [2] 下载音频 ──
    def download_audio(self, url: str, name: str, tag: str = None,
                       timeout: int = 300) -> str:
        """yt-dlp bestaudio 下载，返回音频文件路径

        tag: 候选唯一标识（如 bvid），文件名按 tag 隔离，
             避免 yt-dlp 对同名文件跳过下载返回上一个候选的旧音频
        """
        os.makedirs(TMP_DIR, exist_ok=True)
        out_tpl = os.path.join(TMP_DIR, f'audio_{name}_{tag or "src"}')
        cmd = [
            'yt-dlp', '-f', 'bestaudio/best', '--no-playlist', '-q',
            '-o', out_tpl + '.%(ext)s', url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        files = [f for f in glob.glob(out_tpl + '.*')
                 if not f.endswith('.part') and os.path.getsize(f) > 100000]
        if proc.returncode != 0 or not files:
            raise RuntimeError(f'yt-dlp 下载失败: {proc.stderr.strip()[-200:]}')
        return files[0]

    # ── [3] VAD 切段 ──
    def select_segment(self, audio_path: str, scan_seconds: int = 300) -> dict:
        """faster-whisper VAD → 挑 5-10s 连续纯人声段

        策略: 扫描前 scan_seconds 秒 → VAD 分段 → 相邻合并 →
              候选 = 5-12s 的语音块（越接近 8s 越好）→ 跳过含音乐标记 →
              按均分排序返回最优
        """
        from utils.stt import transcribe_audio

        os.makedirs(TMP_DIR, exist_ok=True)
        scan_wav = os.path.join(TMP_DIR, f'scan_{os.getpid()}.wav')
        subprocess.run(
            ['ffmpeg', '-y', '-t', str(scan_seconds), '-i', audio_path,
             '-ar', '24000', '-ac', '1', '-sample_fmt', 's16', scan_wav],
            capture_output=True, timeout=120,
        )

        segments = transcribe_audio(scan_wav, language='zh')
        if not segments:
            raise RuntimeError('VAD 未检出语音段')

        # 过滤纯器乐段（whisper 把无歌词音乐转写成 ♪），作为语音块分界
        speech = [s for s in segments if s['text'].strip('♪ ')]

        # 相邻段合并（间隔 <0.4s）成语音块（解说语速连续，可能合并成巨块）
        blocks = []
        for seg in speech:
            if blocks and seg['start'] - blocks[-1]['end'] < 0.4:
                blocks[-1]['end'] = seg['end']
                blocks[-1]['text'] += seg['text']
            else:
                blocks.append({'start': seg['start'], 'end': seg['end'],
                               'text': seg['text']})

        # 候选窗口：5-12s 语音块；>12s 巨块取前 8s + 中间 8s 两个窗口
        candidates = []
        for b in blocks:
            dur = b['end'] - b['start']
            if 5.0 <= dur <= 12.0:
                candidates.append({'start': b['start'], 'end': b['end'],
                                   'duration': dur, 'text': b['text']})
            elif dur > 12.0:
                candidates.append({'start': b['start'], 'end': b['start'] + 8.0,
                                   'duration': 8.0, 'text': b['text'][:40]})
                mid = b['start'] + (dur - 8.0) / 2
                candidates.append({'start': mid, 'end': mid + 8.0,
                                   'duration': 8.0, 'text': '(mid)' + b['text'][:40]})

        # 跳过片头 3s（可能有片头曲）+ 按接近 8s 排序
        candidates = [c for c in candidates if c['start'] > 3.0]
        candidates.sort(key=lambda c: abs(c['duration'] - 8.0))
        os.remove(scan_wav)
        if not candidates:
            raise RuntimeError('未找到 5-10s 合格语音段')
        return candidates[0]

    # ── [4] 裁剪 + 转格式 ──
    def cut_segment(self, src: str, start: float, dur: float, out_wav: str) -> str:
        """ffmpeg 裁剪 → CosyVoice 要求格式（24kHz mono 16bit wav）"""
        subprocess.run(
            ['ffmpeg', '-y', '-ss', f'{start:.2f}', '-t', f'{dur:.2f}', '-i', src,
             '-ar', '24000', '-ac', '1', '-sample_fmt', 's16', out_wav],
            capture_output=True, timeout=60,
        )
        if not os.path.isfile(out_wav):
            raise RuntimeError('ffmpeg 裁剪失败')
        return out_wav

    # ── [5] 转写 prompt_text ──
    def transcribe_segment(self, wav_path: str) -> str:
        from utils.stt import transcribe_audio
        segs = transcribe_audio(wav_path, language='zh')
        return ''.join(s['text'] for s in segs).strip()

    # ── [6] 入库注册 ──
    def register(self, name: str, wav_path: str, prompt_text: str,
                 desc: str = '', **meta) -> str:
        """写入 voices/{name}/prompt.wav + meta.json（TTS server 扫描注册 cosy-{name}）"""
        vdir = os.path.join(VOICE_DIR, name)
        os.makedirs(vdir, exist_ok=True)
        dst_wav = os.path.join(vdir, 'prompt.wav')
        subprocess.run(['cp', wav_path, dst_wav], check=True)

        meta_full = {
            'prompt_text': prompt_text,
            'desc': desc or name,
            'created_at': datetime.now().isoformat(),
            'sample_rate': 24000,
            **meta,
        }
        with open(os.path.join(vdir, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta_full, f, ensure_ascii=False, indent=2)
        return dst_wav

    # ── [7] 质量校验 ──
    def test_voice(self, name: str, text: str = None, save_sample: bool = True) -> dict:
        """用克隆音色合成测试句 → voice_samples/ 试听

        优先走已注册音色（cosy-{name}），server 未热加载则直接传 prompt_wav/prompt_text
        """
        from utils.tts import TTSClient

        vdir = os.path.join(VOICE_DIR, name)
        meta_file = os.path.join(vdir, 'meta.json')
        if not os.path.isfile(meta_file):
            raise FileNotFoundError(f'音色未注册: voices/{name}/meta.json')
        meta = json.load(open(meta_file, encoding='utf-8'))
        prompt_wav = os.path.join(vdir, 'prompt.wav')
        prompt_text = meta.get('prompt_text', '')

        client = TTSClient(self.config)
        sample_path = None
        if save_sample:
            os.makedirs(SAMPLE_DIR, exist_ok=True)
            safe_desc = (meta.get('desc', name))[:30]
            sample_path = os.path.join(SAMPLE_DIR, f'cosy-{name}__{safe_desc}.wav')
        result = client.synthesize(
            text or TEST_TEXT, voice=f'cosy-{name}',
            prompt_wav=prompt_wav, prompt_text=prompt_text,
            output_path=sample_path,
        )

        if sample_path:
            result['audio_path'] = sample_path

        result['voice_desc'] = meta.get('desc', name)
        return result

    # ── 完整流水线 ──
    def clone(self, keyword: str, name: str, index: int = 0, desc: str = None,
              scan_seconds: int = 300) -> dict:
        """搜索 → 下载 → 切段 → 转写 → 注册 → 测试，返回完整报告

        下载/切段失败自动尝试下一个候选（最多 3 个）
        """
        t0 = time.time()
        report = {'name': name, 'keyword': keyword, 'steps': {}}

        # [1] 搜索
        sources = self.search_sources(keyword, top=10)
        if not sources:
            raise RuntimeError(f'搜索无结果: {keyword}')
        report['steps']['search'] = {'count': len(sources),
                                     'candidates': [s['title'][:40] for s in sources[:5]]}
        print(f'  [1/6] 搜索: {len(sources)} 个候选')

        # [2-5] 下载 → 切段 → 转写（带重试）
        picked = None
        errors = []
        for cand in sources[index:index + 3]:
            try:
                print(f'  [2/6] 下载: {cand["title"][:40]} ({cand["author"]})')
                audio = self.download_audio(cand['url'], name, tag=cand['bvid'])

                print('  [3/6] VAD 切段...')
                seg = self.select_segment(audio, scan_seconds=scan_seconds)

                tmp_wav = os.path.join(TMP_DIR, f'seg_{name}.wav')
                os.makedirs(TMP_DIR, exist_ok=True)
                self.cut_segment(audio, seg['start'], seg['duration'], tmp_wav)

                vol = _vol_detect(tmp_wav)
                if vol < -32.0:
                    errors.append(f'{cand["bvid"]}: 段太安静 ({vol}dB)')
                    continue

                print('  [4/6] 转写 prompt_text...')
                prompt_text = self.transcribe_segment(tmp_wav)
                if len(prompt_text) < 8:
                    errors.append(f'{cand["bvid"]}: 转写太短 ({prompt_text})')
                    continue

                picked = {
                    'source': cand, 'segment': seg,
                    'prompt_text': prompt_text, 'wav': tmp_wav,
                }
                break
            except Exception as e:
                errors.append(f'{cand["bvid"]}: {e}')
                continue
        if picked is None:
            raise RuntimeError(f'所有候选失败: {errors}')
        report['steps']['source'] = picked['source']
        report['steps']['segment'] = picked['segment']
        report['steps']['errors'] = errors

        # [6] 注册
        wav_path = self.register(
            name, picked['wav'], picked['prompt_text'],
            desc=desc or f'{keyword}-{picked["source"]["author"]}',
            source_url=picked['source']['url'],
            source_title=picked['source']['title'],
            source_up=picked['source']['author'],
        )
        report['steps']['register'] = {
            'voice_dir': os.path.join(VOICE_DIR, name),
            'prompt_wav': wav_path,
            'prompt_text': picked['prompt_text'],
        }
        print(f'  [5/6] 注册: cosy-{name} ← {picked["prompt_text"][:30]}...')

        # [7] 测试合成
        print('  [6/6] 测试合成...')
        test = self.test_voice(name)
        report['steps']['test'] = {
            'sample': test.get('audio_path'),
            'duration': test.get('duration'),
            'text': TEST_TEXT,
        }
        report['elapsed'] = round(time.time() - t0, 1)
        print(f'  ✅ 完成: {report["elapsed"]}s, 试听样本: {test.get("audio_path")}')
        return report

    def list_voices(self) -> list:
        """列出已注册的自定义音色"""
        voices = []
        if not os.path.isdir(VOICE_DIR):
            return voices
        for name in sorted(os.listdir(VOICE_DIR)):
            meta_file = os.path.join(VOICE_DIR, name, 'meta.json')
            if not os.path.isfile(meta_file):
                continue
            meta = json.load(open(meta_file, encoding='utf-8'))
            voices.append({
                'name': name, 'voice_key': f'cosy-{name}',
                'desc': meta.get('desc', name),
                'prompt_text': (meta.get('prompt_text', '') or '')[:40],
                'source': meta.get('source_title', ''),
            })
        return voices


def main():
    parser = argparse.ArgumentParser(description='声音克隆生产化（v4 M3）')
    sub = parser.add_subparsers(dest='cmd')

    p_search = sub.add_parser('search', help='搜索人声源')
    p_search.add_argument('--keyword', '-k', required=True)
    p_search.add_argument('--top', '-n', type=int, default=10)
    p_search.add_argument('--min-dur', type=float, default=180)

    p_clone = sub.add_parser('clone', help='完整克隆流水线')
    p_clone.add_argument('--keyword', '-k', required=True)
    p_clone.add_argument('--name', required=True)
    p_clone.add_argument('--index', type=int, default=0, help='用第 N 个候选')
    p_clone.add_argument('--desc', default=None)
    p_clone.add_argument('--scan-seconds', type=int, default=300)

    p_test = sub.add_parser('test', help='测试已注册音色')
    p_test.add_argument('--name', required=True)
    p_test.add_argument('--text', default=None)

    sub.add_parser('list', help='列出已注册音色')

    args = parser.parse_args()
    vc = VoiceCloner()

    if args.cmd == 'search':
        sources = vc.search_sources(args.keyword, top=args.top, min_dur=args.min_dur)
        print(f'=== {len(sources)} 个候选 ===')
        for i, s in enumerate(sources):
            print(f'  [{i}] {s["title"][:50]} | {s["author"]} | '
                  f'{s["duration"]:.0f}s | play={s["play"]} | {s["bvid"]}')
    elif args.cmd == 'clone':
        report = vc.clone(args.keyword, args.name, index=args.index,
                          desc=args.desc, scan_seconds=args.scan_seconds)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    elif args.cmd == 'test':
        result = vc.test_voice(args.name, text=args.text)
        print(f'voice: cosy-{args.name}')
        print(f'duration: {result["duration"]}s')
        print(f'sample: {result.get("audio_path")}')
    elif args.cmd == 'list':
        voices = vc.list_voices()
        print(f'=== {len(voices)} 个自定义音色 ===')
        for v in voices:
            print(f'  {v["voice_key"]:24s} {v["desc"]} | 源: {v["source"][:30]}')
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
