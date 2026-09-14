#!/usr/bin/env python3
"""快速营销号链路 — 图片 + 运镜动效组成视频，跳过视频模型（v4 M6）

核心思路：静态参考图 → ffmpeg zoompan(Ken Burns 推拉摇移)生成"伪动态"片段
→ 拼接成片 + 旁白 TTS + 字幕 + BGM。**缺省不调 Wan/I2V 视频模型**，
出片分钟级，主打速度和量。

用法:
  from utils.fastline import FastLine
  fl = FastLine()
  meta = fl.run("热点概念/文案", output_path=out.mp4)   # 自动爬图 + LLM出文案 + 出片
  meta = fl.run(concept, images_dir='dir/', ...)         # 用自己准备的图
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm import LLMClient
from utils.tts import TTSClient
from utils.asset_crawler import AssetCrawler
from utils.rife import RIFEClient
from core.postprocess import PostProcessor
from utils.ffmpeg_tools import compose

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'config', 'config.json')

FFMPEG = 'ffmpeg'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


class FastLine:
    """快速营销号链路器：图片 + 运镜 + 旁白 + BGM，跳过视频模型。"""

    def __init__(self, config=None, llm=None):
        self.config = config or load_config()
        self.llm = llm or LLMClient(self.config)
        self.tts = TTSClient(self.config)
        self.crawler = AssetCrawler()
        self.post = PostProcessor(self.config, llm=self.llm)
        self.output_dir = self.config['output_dir']
        self.i2v = self.config.get('i2v_defaults', {})

    # ══ 1. LLM 出旁白 + 每段运镜 ══
    def plan_narration(self, concept, n, motion_override=None) -> list:
        """LLM 把旁白拆成 n 段（每段对应一张图），附每段运镜。

        返回 [{text, motion}]，text=该图配的旁白，motion=pan/zoom-in/zoom-out。
        无 llm 或失败时退化为均匀切分概念文本 + 默认 motion。
        """
        motions = ['pan', 'zoom-in', 'zoom-out'] if motion_override is None \
            else [motion_override] * n
        prompt = [
            {'role': 'system', 'content':
             '你是营销号短视频文案策划。把给定概念/主题写成 n 句押韵有冲击力的旁白短句，'
             '每句配一种运镜。只输出 JSON：{"segments":[{"text":"短句","motion":"pan"}...]}，'
             '每段 text 控制在 8-18 字，motion 取值 pan/zoom-in/zoom-out。'},
            {'role': 'user', 'content': f'主题: {concept}\n分 {n} 段旁白 + 每段运镜'},
        ]
        try:
            r = self.llm.chat_json(prompt)
            segs = r if isinstance(r, list) else r.get('segments')
            if not segs:
                raise ValueError('empty segments')
            out = []
            for i, s in enumerate(segs[:n]):
                text = str(s.get('text', '')).strip()
                if not text:
                    continue
                m = s.get('motion', motions[i % len(motions)])
                if m not in ('pan', 'zoom-in', 'zoom-out'):
                    m = 'zoom-in'
                out.append({'text': text, 'motion': m})
            if out:
                return out
        except Exception as e:
            print(f'  ⚠ 旁白规划失败: {e}，退化为概念切分')
        # 退化：概念原文按标点切 n 段，每段默认 zoom-in
        parts = _split_text(concept, n)
        return [{'text': p or '画面', 'motion': 'zoom-in'} for p in parts]

    # ══ 2. 抓取/收集图片 ══
    def collect_images(self, concept, images_dir, n, save_dir) -> list:
        """返回 n 张图绝对路径列表。images_dir 有图则直接用，否则必应爬取。"""
        if images_dir and os.path.isdir(images_dir):
            exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
            files = sorted(
                os.path.join(images_dir, f)
                for f in os.listdir(images_dir)
                if f.lower().endswith(exts) and os.path.isfile(os.path.join(images_dir, f)))
            if not files:
                print('  ⚠ images_dir 无图片，转爬取')
            else:
                print(f'  [img] 使用已有图 {len(files)} 张')
                return _pad_list(files, n) if len(files) >= n else files[:]
        # 爬取
        os.makedirs(save_dir, exist_ok=True)
        print(f'  [crawl] 为概念爬参考图 x{n} ...')
        items = self.crawler.crawl_concept_refs(concept, save_dir, top=n, llm=self.llm)
        if not items:
            raise RuntimeError('图片爬取失败（网络/无结果），请 --images 自行提供')
        return _pad_list([it['path'] for it in items], n)

    # ══ 3. 单图 → zoompan 伪动态片段 ══
    def _kenburns(self, img_path, out_path, motion, seconds, width=832, height=480,
                  fps=24, crf=18):
        """静态图 → Ken Burns 推拉摇移动态片段（纯 ffmpeg，CPU）。"""
        scale = f'scale={width}:{height}:force_original_aspect_ratio=increase,'
        crop = f'crop={width}:{height},'
        total = max(int(seconds * fps), 30)  # 总帧数

        if motion == 'pan':
            # 放大 1.3x 后水平移动（左→右）
            z = '1.30'
            x = f"(iw-iw/zoom)*on/{total}"
            y = '(ih-ih/zoom)/2'
        elif motion == 'zoom-out':
            z = 'max(1.30-0.30*on/{},1.0)'.format(total)
            x = 'iw/2-(iw/zoom/2)'
            y = 'ih/2-(ih/zoom/2)'
        else:  # zoom-in
            z = 'min(1.0+0.30*on/{},1.30)'.format(total)
            x = 'iw/2-(iw/zoom/2)'
            y = 'ih/2-(ih/zoom/2)'

        vf = (f'{scale}{crop}'
              f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps},"
              f'format=yuv420p')
        cmd = [
            FFMPEG, '-y', '-loop', '1', '-framerate', str(fps), '-i', img_path,
            '-vf', vf, '-t', str(seconds),
            '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf),
            '-pix_fmt', 'yuv420p', '-an', out_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=int(seconds * fps / 24) + 120)
        except Exception as e:
            subprocess.run(cmd, capture_output=True, timeout=180)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1000:
            raise RuntimeError(f'Ken Burns 生成失败: {img_path}')

    # ══ 4. 每段 TTS 旁白 ══
    def _tts_segment(self, text, out_path, voice, speed):
        r = self.tts.synthesize(text, output_path=out_path, voice=voice, speed=speed)
        return r.get('audio_path', out_path)

    # ══ 5. 主跑 ══
    def run(self, concept, output_path=None, images_dir=None, n=5,
            voice=None, speed=1.0, motion=None, seconds=None,
            task_id=None, no_rife=False, no_color=False, bgm_override=None,
            no_bgm=False, use_stt=False, lut_override=None, slowmo=None) -> dict:
        """一键出片。返回 meta。"""
        start = time.time()
        task_id = task_id or datetime.now().strftime('f%Y%m%d_%H%M%S')
        task_dir = os.path.join(self.output_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        clips_dir = os.path.join(task_dir, 'clips')
        os.makedirs(clips_dir, exist_ok=True)

        # 1) 旁白规划
        print(f'\n=== [fast] 旁白规划（{n} 段）===')
        segments = self.plan_narration(concept, n, motion_override=motion)
        n = len(segments)
        print(f'  {n} 段旁白: ' + ' | '.join(s["text"] for s in segments))

        # 2) 图片
        print(f'\n=== [fast] 图片（{n} 张）===')
        images = self.collect_images(concept, images_dir, n, task_dir)
        images = _pad_list(images, n)

        # 3) 每段 TTS + Ken Burns
        print(f'\n=== [fast] TTS + Ken Burns（{n} 段）===')
        clips, audios = [], []
        per_sec = seconds if seconds else max(2.5, round(4.0 * (24.0 / n) + 1.2, 1))
        wait_tts = False
        for i, (img, seg) in enumerate(zip(images, segments), 1):
            print(f'  [{i}/{n}] "{seg["text"]}" motion={seg["motion"]}')
            a = self._tts_segment(seg['text'], os.path.join(clips_dir, f'seg_{i}.wav'),
                                  voice or self.config['tts']['default_voice'], speed)
            dur = 0.0
            if os.path.isfile(a):
                r = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'csv=p=0', a], capture_output=True, text=True)
                dur = float(r.stdout.strip() or 0)
            clip_sec = max(dur + 0.6, per_sec)
            c = os.path.join(clips_dir, f'seg_{i}.mp4')
            self._kenburns(img, c, seg['motion'], clip_sec)
            clips.append(c)
            audios.append(a)
            wait_tts = True

        # 4) 合成原始视频（含字幕，非 STT 用估算时间轴）
        print(f'\n=== [fast] 合成成片 ===')
        transition_types = ['crossfade'] * max(0, len(clips) - 1)
        srt_path = None if use_stt else self._build_srt(segments, clips_dir)
        final_path = os.path.join(task_dir, 'final_raw.mp4')
        compose(clips, audios, srt_path=srt_path, output_path=final_path,
                transition_types=transition_types)

        # 5) 后处理：LUT + BGM + (可选 STT) —— 复用 PostProcessor（含 LLM 自动选）
        print(f'\n=== [fast] 后处理（LUT/BGM/STT）===')
        self.post.run_all(
            final_path, clips, clips_dir, task_id, task_dir,
            concept=concept, no_rife=True,
            lut_override=lut_override, no_color=no_color,
            bgm_override=bgm_override, no_bgm=no_bgm, use_stt=use_stt,
        )

        # 6) 可选 slowmo（RIFE，GPU2，默认关）
        if slowmo and slowmo > 1:
            print(f'\n=== [fast] RIFE slowmo x{slowmo} ===')
            try:
                slow_path = os.path.join(task_dir, 'final_slow.mp4')
                client = RIFEClient(config=self.config)
                client.slowmo(final_path, slow_path, multiplier=slowmo)
                final_path = slow_path
            except Exception as e:
                print(f'  ⚠ slowmo 失败: {e}，用原片')

        # 7) 定稿
        out = output_path
        if out is None:
            out = os.path.join(task_dir, 'final.mp4')
        elif not os.path.isabs(out):
            out = os.path.join(self.output_dir, out)
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        if os.path.normpath(final_path) != os.path.normpath(out):
            shutil.move(final_path, out)

        meta = {
            'task_id': task_id,
            'concept': concept,
            'mode': 'fast',
            'segments': segments,
            'clips': clips,
            'audio': audios,
            'images': images,
            'output': out,
            'duration': _video_duration(out),
            'elapsed_s': round(time.time() - start, 1),
        }
        print(f'\nDone: {out} ({meta["duration"]:.1f}s, {meta["elapsed_s"]}s)')
        with open(os.path.join(task_dir, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    # ══ 辅助 ══
    def _build_srt(self, segments, clips_dir):
        """基于 TTS 时间戳估算每段字幕起止（无 STT 时用近似）。"""
        ts = []
        t = 0.0
        for i, seg in enumerate(segments):
            a = os.path.join(clips_dir, f'seg_{i+1}.wav')
            dur = 0.0
            if os.path.isfile(a):
                r = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'csv=p=0', a], capture_output=True, text=True)
                dur = float(r.stdout.strip() or 0)
            ts.append({'start': t, 'end': t + dur + 0.4, 'text': seg['text']})
            t += dur + 0.4
        srt = os.path.join(clips_dir, 'subtitle.srt')
        with open(srt, 'w', encoding='utf-8') as f:
            for i, x in enumerate(ts, 1):
                f.write(f'{i}\n{srt_time(x["start"])} --> {srt_time(x["end"])}\n{x["text"]}\n\n')
        return srt


def srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def _video_duration(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _split_text(text, n):
    """把长文本按标点切成尽量均匀的 n 段。"""
    import re
    sentences = [s for s in re.split(r'[。！？!?；;，,\n]', text) if s.strip()]
    if not sentences:
        return []
    if len(sentences) >= n:
        return sentences[:n]
    # 不够 n 句则合并/复用
    out = list(sentences)
    while len(out) < n:
        out.append(out[-1])
    return out


def _pad_list(lst, n):
    """不足 n 循环补足（末尾重复最后一项），多于裁剪。"""
    if not lst:
        return lst
    out = list(lst)
    while len(out) < n:
        out.append(out[-1])
    return out[:n]


def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog='fastline',
        description='Vidance 快速营销号链路（图片+RIFE，跳过视频模型）')
    parser.add_argument('concept', help='热点概念/旁白主题')
    parser.add_argument('--images', default=None, help='图片目录（有图则用，否则必应爬取）')
    parser.add_argument('-o', '--output', default=None, help='输出视频路径')
    parser.add_argument('-n', '--images-count', type=int, default=5, help='图片/段落数')
    parser.add_argument('--voice', default=None, help='TTS 音色')
    parser.add_argument('--speed', type=float, default=1.0, help='旁白语速')
    parser.add_argument('--motion', default=None, choices=['pan', 'zoom-in', 'zoom-out'],
                        help='统一运镜（默认 LLM 每段自选）')
    parser.add_argument('--seconds', type=float, default=None, help='每段时长(秒)')
    parser.add_argument('--slowmo', type=int, default=None, help='RIFE slowmo 倍率(需 GPU2)')
    parser.add_argument('--no-rife', action='store_true', help='禁用 RIFE 过渡')
    parser.add_argument('--lut', default=None, help='LUT 风格')
    parser.add_argument('--no-color', action='store_true', help='禁用调色')
    parser.add_argument('--bgm', default=None, help='BGM mood')
    parser.add_argument('--no-bgm', action='store_true', help='禁用 BGM')
    parser.add_argument('--stt', action='store_true', help='STT 字幕对齐')
    parser.add_argument('--task-id', default=None)
    args = parser.parse_args()

    fl = FastLine()
    meta = fl.run(
        args.concept, output_path=args.output, images_dir=args.images,
        n=args.images_count, voice=args.voice, speed=args.speed,
        motion=args.motion, seconds=args.seconds, task_id=args.task_id,
        no_rife=args.no_rife, no_color=args.no_color, bgm_override=args.bgm,
        no_bgm=args.no_bgm, use_stt=args.stt, lut_override=args.lut,
        slowmo=args.slowmo,
    )


if __name__ == '__main__':
    main()
