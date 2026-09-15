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
    def collect_images(self, concept, images_dir, n, save_dir,
                       images_source='crawl', segments=None) -> list:
        """返回 n 张图绝对路径列表。

        images_dir 有图则直接用；否则按 images_source:
          'crawl' → 必应爬图（默认，贴合真实热点画面）；不足 n 时用 FLUX 补足缺帧
          'flux'  → 直接 FLUX 逐段文生图（质量可控不依赖爬网）
        """
        if images_dir and os.path.isdir(images_dir):
            exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
            files = sorted(
                os.path.join(images_dir, f)
                for f in os.listdir(images_dir)
                if f.lower().endswith(exts) and os.path.isfile(os.path.join(images_dir, f)))
            if not files:
                print('  ⚠ images_dir 无图片，转 images_source')
            else:
                print(f'  [img] 使用已有图 {len(files)} 张')
                return _pad_list(files, n) if len(files) >= n else files[:]

        os.makedirs(save_dir, exist_ok=True)

        imgs = []
        if images_source == 'crawl':
            # Pexels 图源（首选，精准对题且已 md5 去重）——直接取 n 张，无需逐张慢速多模态校验，
            # 总数严格 ≤ n，速度快。
            print(f'  [crawl] 为概念取参考图（目标 {n} 张，Pexels 图源）...')
            try:
                items = self.crawler.download_images(concept, save_dir, top=n)
                imgs = [it['path'] for it in items]
            except Exception as e:
                print(f'  ⚠ [图源缺省] 爬图服务不可用（{e}），转 FLUX/纯色兜底')
                imgs = []
            print(f'  [crawl] 取到 {len(imgs)} 张')
            if len(imgs) < n:
                lack = n - len(imgs)
                try:
                    print(f'  [topup] 图源不足，用 FLUX 补齐 {lack} 张')
                    imgs += self._gen_flux_images(concept, lack, save_dir, segments=segments)
                except Exception as e:
                    print(f'  ⚠ [FLUX 缺省] FLUX 不可用（{e}），用纯色图兜底 {lack} 张')
                    imgs += [self._gen_placeholder_image(save_dir, i, concept)
                             for i in range(lack)]
        else:  # 'flux'
            try:
                imgs = self._gen_flux_images(concept, n, save_dir, segments=segments)
            except Exception as e:
                print(f'  ⚠ [FLUX 缺省] FLUX 不可用（{e}），用纯色图兜底 {n} 张')
                imgs = [self._gen_placeholder_image(save_dir, i, concept)
                        for i in range(n)]

        if not imgs:
            raise RuntimeError('图片获取失败，请 --images 自行提供或检查网络/8192')
        return _pad_list(imgs, n)[:n]

    @staticmethod
    def _gen_placeholder_image(save_dir, idx, concept=''):
        """纯色渐变占位图（服务缺省兜底，1280x720 jpg）"""
        from PIL import Image, ImageDraw
        colors = [(28, 32, 48), (44, 40, 72), (24, 48, 60), (52, 36, 48)]
        base = colors[idx % len(colors)]
        img = Image.new('RGB', (1280, 720), base)
        d = ImageDraw.Draw(img)
        # 底部渐变亮带 + 概念水印，避免纯色死板
        for y in range(720 - 160, 720):
            t = (y - (720 - 160)) / 160
            c = tuple(int(base[i] + (255 - base[i]) * t * 0.25) for i in range(3))
            d.line([(0, y), (1280, y)], fill=c)
        try:
            d.text((40, 40), f'({idx + 1}) {concept[:24]}', fill=(200, 200, 220))
        except Exception:
            pass
        path = os.path.join(save_dir, f'placeholder_{idx}.jpg')
        img.save(path, quality=88)
        return path

    def _gen_flux_images(self, concept, n, save_dir, segments=None,
                         max_retry=2) -> list:
        """FLUX 逐段文生图（横向），并接入"图-旁白相关性审核"。

        - 每段旁白先翻译成英文 FLUX prompt（optimize_fastline_prompt），避免中文语义偏差
        - 生成后用多模态 LLM（assess_image_relevance）校验该图是否贴合该段旁白主体，
          不通过则换 seed 重生成（≤max_retry 次）
        """
        from utils.comfy_api import ComfyClient
        flux = ComfyClient(config=self.config, instance='flux')
        out = []
        prompts = [s['text'] for s in (segments or [])]
        while len(prompts) < n:
            prompts.append(concept)
        for i, p in enumerate(prompts[:n], 1):
            path = os.path.join(save_dir, f'flux_{i}.png')
            en = self.llm.optimize_fastline_prompt(p, concept)
            print(f'  [flux] 生成图 {i}/{n}: {p}\n        en="{en}"')
            ok = False
            for attempt in range(max_retry + 1):
                try:
                    flux.generate_flux_t2i(
                        en, width=1024, height=768, steps=20,
                        filename_prefix='vidance/fastline', output_path=path)
                except Exception as e:
                    print(f'    ⚠ FLUX 生成失败: {e}')
                    break
                if not os.path.isfile(path):
                    print('    ⚠ 生成无输出文件')
                    break
                # 审核：这张图是否贴合该段旁白主体
                rel = self.llm.assess_image_relevance(path, p)
                if rel['pass']:
                    print(f"    ✅ 图{path.split('/')[-1]} 审核通过 score={rel['score']}")
                    ok = True
                    break
                print(f"    ✗ 审核不达(score={rel['score']} {rel['reason'][:24]}), 重生成")
            if ok:
                out.append(path)
        if not out:
            raise RuntimeError('FLUX 文生图全部未通过审核，请确认 8192 在线或换概念')
        return _pad_list(out, n)

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
    def _tts_segment(self, text, out_path, voice, speed, fallback_seconds=3.0):
        """TTS 合成。服务不可用时生成静音音频兜底（流程不崩，打印缺省警告）。"""
        try:
            r = self.tts.synthesize(text, output_path=out_path, voice=voice, speed=speed)
            return r.get('audio_path', out_path)
        except Exception as e:
            print(f'  ⚠ [TTS 缺省] TTS 服务不可用（{e}），段音频用静音 {fallback_seconds}s 兜底')
            print(f'    （启动 TTS: CUDA_VISIBLE_DEVICES=2 TTS_FP16=1 python utils/tts_server.py）')
            self._gen_silence(out_path, fallback_seconds)
            return out_path

    @staticmethod
    def _gen_silence(out_path, seconds):
        """ffmpeg 生成静音 wav（24kHz mono 16bit，与 TTS 输出格式一致）"""
        subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i',
             f'anullsrc=r=24000:cl=mono', '-t', str(seconds),
             '-c:a', 'pcm_s16le', out_path],
            capture_output=True, timeout=30,
            check=True,
        )

    # ══ 5. 主跑 ══
    def run(self, concept, output_path=None, images_dir=None, n=5,
            voice=None, speed=1.0, motion=None, seconds=None,
            task_id=None, no_rife=False, no_color=False, bgm_override=None,
            no_bgm=False, use_stt=False, lut_override=None, slowmo=None,
            images_source='crawl', source_topic=None, trend_date=None,
            show_source=True, effects='off') -> dict:
        """一键出片。返回 meta。

        images_source: 'crawl'(必应爬图，默认，不足时 FLUX 补足) / 'flux'(纯 FLUX 文生图)。
        slowmo>1 时保留原片 final.mp4，另出 RIFE 版 final_slow.mp4（都不删）。
        source_topic: 对应热搜标题（如实供，写进 meta + 可选片头水印，便于验证真实性）。
        trend_date: 热搜日期（如 '2026-09-14'），写进 meta + 片头水印。
        show_source: 是否在片头烧录 "{trend_date} 热搜: {source_topic}"。
        """
        start = time.time()
        task_id = task_id or datetime.now().strftime('f%Y%m%d_%H%M%S')
        trend_date = trend_date or datetime.now().strftime('%Y-%m-%d')
        task_dir = os.path.join(self.output_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        clips_dir = os.path.join(task_dir, 'clips')
        os.makedirs(clips_dir, exist_ok=True)

        # 1) 旁白规划
        print(f'\n=== [fast] 旁白规划（{n} 段）===')
        segments = self.plan_narration(concept, n, motion_override=motion)
        n = len(segments)
        print(f'  {n} 段旁白: ' + ' | '.join(s["text"] for s in segments))
        if source_topic:
            print(f'  热搜来源: {trend_date}: {source_topic}')

        # 2) 图片
        print(f'\n=== [fast] 图片（{n} 张，source={images_source}）===')
        images = self.collect_images(concept, images_dir, n, task_dir,
                                     images_source=images_source, segments=segments)
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

        # 3.5) v5 剪辑特效：--effects auto 由 LLM 选每镜特效并施加
        fx_meta = {'effects': ['none'] * len(clips), 'transitions': []}
        if effects == 'auto':
            print(f'\n=== [v5] 剪辑特效（LLM 自动选）===')
            from utils.effects import apply_effects_to_clips
            pseudo = {'title': concept, 'shots': [
                {'id': i + 1, 'scene_desc': s['text'], 'emotion': ''}
                for i, s in enumerate(segments)]}
            plan = self.llm.select_effects(pseudo, None, None)
            effs = plan['shots'][:len(clips)]
            fx_dir = os.path.join(clips_dir, 'fx')
            clips = apply_effects_to_clips(clips, fx_dir, effs)
            fx_meta['effects'] = effs
            fx_meta['transitions'] = plan['transitions']

        # 4) 合成原始视频（含字幕，非 STT 用估算时间轴）
        print(f'\n=== [fast] 合成成片 ===')
        transition_types = ['crossfade'] * max(0, len(clips) - 1)
        # 热搜来源不占字幕时间轴（避免字幕/声音错位），走顶部角标（步骤 5.5）
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

        # 5.5) 片头/顶部热搜来源角标（drawtext，独立于字幕，不造成错位）
        if show_source and source_topic:
            print(f'\n=== [fast] 热搜来源角标 ===')
            self._overlay_source(final_path, f'{trend_date} 热搜: {source_topic}')

        # 6) 可选 slowmo（RIFE，GPU2，默认关）——原片保留，另出 RIFE 版
        slow_path = None
        if slowmo and slowmo > 1:
            print(f'\n=== [fast] RIFE slowmo x{slowmo} ===')
            slow_path = os.path.join(task_dir, 'final_slow.mp4')
            try:
                client = RIFEClient(config=self.config)
                client.slowmo(final_path, slow_path, multiplier=slowmo)
                print(f'  → {slow_path}')
            except Exception as e:
                print(f'  ⚠ slowmo 失败: {e}，仅保留原片')
                slow_path = None

        # 7) 定稿：原片 final.mp4 恒在；slowmo 时另出 final_slow.mp4（都可看）
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
            'images_source': images_source,
            'segments': segments,
            'clips': clips,
            'audio': audios,
            'images': images,
            'source_topic': source_topic,
            'trend_date': trend_date,
            'effects': fx_meta['effects'],
            'transitions': fx_meta['transitions'],
            'output': out,
            'slowmo_path': slow_path,
            'duration': _video_duration(out),
            'elapsed_s': round(time.time() - start, 1),
        }
        print(f'\nDone: {out} ({meta["duration"]:.1f}s, {meta["elapsed_s"]}s)')
        with open(os.path.join(task_dir, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    # ══ 辅助 ══
    def _overlay_source(self, video_path, text, top_pad=30, font_size=22):
        """在视频顶部角标叠加热搜来源（drawtext，独立于字幕，不占时间轴）。就地替换。"""
        import shutil as _sh
        temp = video_path.replace('.mp4', '_ovl.mp4')
        _sh.move(video_path, temp)
        esc = text.replace(':', r'\:').replace("'", r"\'")
        cmd = [
            FFMPEG, '-y', '-i', temp,
            '-vf', (f"drawtext=text='{esc}':x=(w-text_w)/2:y={top_pad}:"
                    f"fontsize={font_size}:fontcolor=white:borderw=2:bordercolor=black:"
                    f"alpha=0.85"),
            '-c:a', 'copy', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
            '-pix_fmt', 'yuv420p', video_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300)
            if os.path.isfile(video_path):
                os.remove(temp)
                print(f'  顶部角标: {text}')
                return
        except Exception as e:
            print(f'  ⚠ 角标叠加失败: {e}')
        if os.path.isfile(temp):
            _sh.move(temp, video_path)

    def _build_srt(self, segments, clips_dir):
        """基于 TTS 时间戳估算每段字幕起止（无 STT 时用近似）；从 0s 依次排，保证与配音对齐。"""
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
    parser.add_argument('--images-source', default='crawl',
                        choices=['crawl', 'flux'],
                        help='图片来源: crawl(必应爬图，默认，不足时 FLUX 补足) / flux(FLUX 文生图)')
    parser.add_argument('--source-topic', default=None,
                        help='对应热搜标题（写进 meta + 片头水印）')
    parser.add_argument('--trend-date', default=None,
                        help='热搜日期（默认今天）')
    parser.add_argument('--no-source', action='store_true',
                        help='不烧录热搜来源水印')
    parser.add_argument('--task-id', default=None)
    args = parser.parse_args()

    fl = FastLine()
    meta = fl.run(
        args.concept, output_path=args.output, images_dir=args.images,
        n=args.images_count, voice=args.voice, speed=args.speed,
        motion=args.motion, seconds=args.seconds, task_id=args.task_id,
        no_rife=args.no_rife, no_color=args.no_color, bgm_override=args.bgm,
        no_bgm=args.no_bgm, use_stt=args.stt, lut_override=args.lut,
        slowmo=args.slowmo, images_source=args.images_source,
        source_topic=args.source_topic, trend_date=args.trend_date,
        show_source=not args.no_source,
    )


if __name__ == '__main__':
    main()
