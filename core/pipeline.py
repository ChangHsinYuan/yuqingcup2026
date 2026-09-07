#!/usr/bin/env python3
"""Vidance v0 流水线主控 — 有声短片端到端生成

流程:
  concept → LLM编剧 → prompt优化 → 逐镜(T2V + TTS + 抽帧) → 审片 → 重试 → 合成

用法:
  python -m core.pipeline "一只猫在月球上跳舞"
  python core/pipeline.py "一只猫在月球上跳舞" --output output/final.mp4
"""
import json
import os
import sys
import time
import random
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm import LLMClient
from utils.tts import TTSClient
from utils.comfy_api import ComfyClient
from utils.ffmpeg_tools import extract_frames, generate_srt, compose

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class Pipeline:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config)
        self.tts = TTSClient(self.config)
        self.comfy = ComfyClient(config=self.config)
        self.output_dir = self.config['output_dir']
        self.wan_defaults = self.config.get('wan_defaults', {})
        self.max_retries = 2

    def run(self, concept: str, output_path: str = None) -> dict:
        """端到端生成有声短片"""
        task_id = time.strftime('%Y%m%d_%H%M%S')
        task_dir = os.path.join(self.output_dir, task_id)
        clips_dir = os.path.join(task_dir, 'clips')
        os.makedirs(clips_dir, exist_ok=True)

        meta = {
            'task_id': task_id,
            'concept': concept,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'running',
            'clips': [],
        }

        print(f'[{task_id}] concept: {concept}')

        # ── 1. 编剧 ──
        print('\n=== [1] 编剧 ===')
        script = self.llm.script_write(concept)
        meta['script'] = script
        print(f'  title: {script["title"]}')
        print(f'  shots: {len(script["shots"])}')

        # ── 2. prompt 优化 ──
        print('\n=== [2] prompt 优化 ===')
        for shot in script['shots']:
            shot['video_prompt'] = self.llm.optimize_prompt(shot['scene_desc'], script['style'])
            print(f'  shot {shot["id"]}: {shot["video_prompt"][:80]}...')

        # ── 3. 逐镜生成 ──
        all_clips = []
        all_audios = []
        all_timestamps = []
        audio_offset = 0.0

        for shot in script['shots']:
            sid = shot['id']
            print(f'\n=== [3] shot {sid} 生成 ===')
            clip_info = self._process_shot(shot, clips_dir, task_id)
            meta['clips'].append(clip_info)
            all_clips.append(clip_info['final_video'])
            all_audios.append(clip_info['audio']['audio_path'])
            for ts in clip_info['audio']['timestamps']:
                all_timestamps.append({
                    'text': ts['text'],
                    'start': round(ts['start'] + audio_offset, 3),
                    'end': round(ts['end'] + audio_offset, 3),
                    'duration': ts['duration'],
                })
            audio_offset += clip_info['audio']['duration']

        # ── 4. 合成 ──
        print('\n=== [4] 合成 ===')
        if output_path is None:
            output_path = os.path.join(task_dir, 'final.mp4')

        srt_path = os.path.join(task_dir, 'subtitle.srt')
        generate_srt(all_timestamps, srt_path)

        compose(
            clips=all_clips,
            audio_paths=all_audios,
            srt_path=srt_path,
            output_path=output_path,
            transition='crossfade',
            transition_duration=0.3,
        )

        meta['output'] = output_path
        meta['srt'] = srt_path
        meta['status'] = 'completed'

        meta_path = os.path.join(task_dir, 'meta.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(f'\n=== 完成 ===')
        print(f'  成片: {output_path}')
        print(f'  元数据: {meta_path}')
        return meta

    def _process_shot(self, shot: dict, clips_dir: str, task_id: str) -> dict:
        """处理单个镜头：T2V + TTS + 抽帧 + 审片 + 重试"""
        sid = shot['id']
        duration = shot.get('duration', 5)
        fps = self.wan_defaults.get('fps', 24)
        length = int(duration * fps)

        attempts = []
        best = None

        for attempt in range(1, self.max_retries + 2):
            seed = random.randint(0, 2**32 - 1)
            video_prefix = f'{task_id}/shot_{sid}'
            video_path = os.path.join(clips_dir, f'shot_{sid}.mp4')
            if attempt > 1:
                video_path = os.path.join(clips_dir, f'shot_{sid}_v{attempt}.mp4')

            print(f'  attempt {attempt}, seed={seed}, length={length}')

            # T2V
            t2v_result = self.comfy.generate_t2v(
                prompt=shot['video_prompt'],
                seed=seed,
                width=self.wan_defaults.get('width', 1280),
                height=self.wan_defaults.get('height', 704),
                length=length,
                steps=self.wan_defaults.get('steps', 20),
                cfg=self.wan_defaults.get('cfg', 5.0),
                fps=fps,
                filename_prefix=video_prefix,
                output_path=video_path,
            )
            print(f'  video: {video_path}')

            # TTS
            audio_path = os.path.join(clips_dir, f'shot_{sid}.wav')
            tts_result = self.tts.synthesize(
                text=shot['narration'],
                output_path=audio_path,
            )
            print(f'  audio: {audio_path} ({tts_result["duration"]:.1f}s)')

            # 抽帧
            frames = extract_frames(video_path, n_frames=4,
                                    output_dir=os.path.join(clips_dir, 'frames'))
            print(f'  frames: {len(frames)}')

            # 审片
            review = self.llm.review_shot(shot['scene_desc'], frames)
            print(f'  review: score={review["score"]}, pass={review["pass"]}')

            attempt_info = {
                'attempt': attempt,
                'seed': seed,
                'video': video_path,
                'review': review,
            }
            attempts.append(attempt_info)

            if best is None or review['score'] > best['review']['score']:
                best = attempt_info

            if review['pass']:
                break

            if attempt <= self.max_retries:
                print(f'  retrying (feedback: {review["feedback"][:60]}...)')
                shot['video_prompt'] = self.llm.optimize_prompt(
                    shot['scene_desc'] + ' ' + review['feedback'],
                    shot.get('style', ''),
                )

        return {
            'shot_id': sid,
            'attempts': attempts,
            'final_video': best['video'],
            'final_score': best['review']['score'],
            'audio': {
                'narration': shot['narration'],
                'audio_path': audio_path,
                'duration': tts_result['duration'],
                'timestamps': tts_result['timestamps'],
            },
        }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Vidance v0 pipeline')
    parser.add_argument('concept', help='Video concept in Chinese')
    parser.add_argument('-o', '--output', default=None, help='Output video path')
    args = parser.parse_args()

    pipeline = Pipeline()
    meta = pipeline.run(args.concept, output_path=args.output)
    print(f'\nDone: {meta["output"]}')
