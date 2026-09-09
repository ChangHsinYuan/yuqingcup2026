#!/usr/bin/env python3
"""Vidance v1 流水线主控 — 角色一致性有声短片生成

流程:
  concept → LLM编剧(含角色描述+camera角度) → 角色锚(FLUX生图→TripoSplat→3DGS)
  → 逐镜(FLUX背景→RenderSplat合成→Wan I2V + TTS + 抽帧) → 审片 → 合成

用法:
  python core/pipeline.py "一个穿红斗篷的少年在雪原上行走" --character "穿红色斗篷的短发少年"
  python core/pipeline.py "一只猫在月球上跳舞" --character "穿宇航服的白猫" -o output/final.mp4
"""
import json
import os
import sys
import time
import random
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm import LLMClient
from utils.tts import TTSClient
from utils.comfy_api import ComfyClient
from utils.splat_renderer import render_splat_at_angle, render_splat_angles
from utils.ffmpeg_tools import extract_frames, generate_srt, compose
from utils.rife import RIFEClient, extract_frame as rife_extract_frame, get_video_frame_count
from utils.music import generate_bgm

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class Pipeline:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config)
        self.tts = TTSClient(self.config)
        self.wan = ComfyClient(config=self.config, instance='wan')
        self.flux = ComfyClient(config=self.config, instance='flux')
        self.output_dir = self.config['output_dir']
        self.wan_defaults = self.config.get('wan_defaults', {})
        self.i2v_defaults = self.config.get('i2v_defaults', {})
        self.flux_defaults = self.config.get('flux_defaults', {})
        self.rife_config = self.config.get('rife', {})
        self.color_config = self.config.get('color', {})
        self.bgm_config = self.config.get('bgm', {})
        self.max_retries = 2

    def run(self, concept: str, output_path: str = None,
            character_desc: str = None, voice: str = None,
            character_mode: str = 'auto', lut_override: str = None,
            bgm_override: str = None) -> dict:
        """端到端生成有声短片

        character_mode:
          'auto' — 先试 3DGS，审查不过自动降级 flux（默认）
          '3dgs' — 强制 3DGS 角色锚（FLUX→TripoSplat→3DGS→RenderSplat→I2V）
          'flux' — 每镜 FLUX 直接生成角色+场景图做 I2V 参考（不重建 3D）
          不传 character_desc 时走 v0 纯 T2V
        """
        task_id = time.strftime('%Y%m%d_%H%M%S')
        task_dir = os.path.join(self.output_dir, task_id)
        clips_dir = os.path.join(task_dir, 'clips')
        char_dir = os.path.join(task_dir, 'character')
        os.makedirs(clips_dir, exist_ok=True)
        os.makedirs(char_dir, exist_ok=True)

        meta = {
            'task_id': task_id,
            'concept': concept,
            'character_desc': character_desc,
            'character_mode': character_mode if character_desc else None,
            'voice': voice,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'running',
            'clips': [],
        }

        print(f'[{task_id}] concept: {concept}')
        if character_desc:
            print(f'  character: {character_desc}')
            print(f'  character_mode: {character_mode}')

        # ── 1. 编剧 ──
        print('\n=== [1] 编剧 ===')
        script = self.llm.script_write(concept, character_desc=character_desc)
        meta['script'] = script
        print(f'  title: {script["title"]}')
        print(f'  shots: {len(script["shots"])}')

        # 如果 LLM 生成了 character.desc 但用户没提供，用 LLM 的
        if not character_desc and 'character' in script:
            character_desc = script['character']['desc']
            meta['character_desc'] = character_desc
            print(f'  character (from LLM): {character_desc}')

        # ── 2. prompt 优化 ──
        print('\n=== [2] prompt 优化 ===')
        style = script.get('style', '')

        # 角色prompt
        character_prompt = None
        if character_desc:
            character_prompt = self.llm.optimize_character_prompt(character_desc)
            script.setdefault('character', {})['prompt'] = character_prompt
            print(f'  character_prompt: {character_prompt[:80]}...')

        # 每镜 video_prompt + background_prompt
        for shot in script['shots']:
            shot['style'] = style
            shot['video_prompt'] = self.llm.optimize_prompt(shot['scene_desc'], style)
            bg_desc = shot.get('background_desc', shot['scene_desc'])
            shot['background_prompt'] = self.llm.optimize_background_prompt(bg_desc, style)
            print(f'  shot {shot["id"]}: video={shot["video_prompt"][:50]}...')
            print(f'           bg={shot["background_prompt"][:50]}...')

        # ── 3. 角色锚阶段 ──
        character = None
        if character_prompt:
            print(f'\n=== [3] 角色锚阶段 (mode={character_mode}) ===')
            character = self._build_character_anchor(
                character_prompt, char_dir, task_id, character_mode
            )
            character['desc'] = character_desc
            meta['character'] = character

            if character['mode'] == '3dgs' and not character.get('review', {}).get('pass', False):
                if character_mode == 'auto':
                    print(f'  ⚠ 3DGS 审查未通过 (score={character["review"]["score"]})，自动降级 flux 模式')
                    character['mode'] = 'flux'
                    meta['character_mode'] = 'flux (auto-downgraded)'
                else:
                    print(f'  ⚠ 3DGS 审查未通过 (score={character["review"]["score"]})，继续 3dgs 模式')

        # ── 4. 逐镜生成 ──
        all_clips = []
        all_audios = []
        all_timestamps = []
        audio_offset = 0.0

        # 4a. 并行预取：FLUX 参考帧 + TTS 配音（与 Wan I2V 独立）
        i2v_w = self.i2v_defaults.get('width', 832)
        i2v_h = self.i2v_defaults.get('height', 480)
        bg_w = self.flux_defaults.get('background_width', i2v_w)
        bg_h = self.flux_defaults.get('background_height', i2v_h)
        prefetched = self._prefetch_shots(
            script['shots'], character, clips_dir, task_id,
            voice, i2v_w, i2v_h, bg_w, bg_h,
        )

        for shot in script['shots']:
            sid = shot['id']
            print(f'\n=== [4] shot {sid} 生成 ===')
            clip_info = self._process_shot(
                shot, clips_dir, task_id, character, voice,
                prefetched=prefetched.get(sid),
            )
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

        # ── 4.5 RIFE 镜头间过渡 ──
        transition_clips = []
        if self.rife_config.get('enabled', False) and len(all_clips) >= 2:
            print(f'\n=== [4.5] RIFE 镜头间过渡 ===')
            transition_clips = self._generate_transitions(all_clips, clips_dir, task_id)

        # ── 5. 合成 ──
        print('\n=== [5] 合成 ===')
        if output_path is None:
            output_path = os.path.join(task_dir, 'final.mp4')

        srt_path = os.path.join(task_dir, 'subtitle.srt')
        generate_srt(all_timestamps, srt_path)

        # ── 5.5 调色 ──
        lut_path = None
        if self.color_config.get('enabled', False) or lut_override:
            print(f'\n=== [5.5] 调色 ===')
            lut_path = self._select_lut(concept, script, task_dir, lut_override)
            if lut_path:
                print(f'  LUT: {os.path.basename(lut_path)}')

        # ── 5.6 配乐 ──
        bgm_path = None
        if self.bgm_config.get('enabled', False) or bgm_override:
            print(f'\n=== [5.6] 配乐 ===')
            total_duration = audio_offset + 2.0
            bgm_path = self._select_bgm(concept, script, task_dir, total_duration, bgm_override)
            if bgm_path:
                print(f'  BGM: {os.path.basename(bgm_path)}')

        compose(
            clips=all_clips,
            audio_paths=all_audios,
            srt_path=srt_path,
            output_path=output_path,
            transition='cut' if transition_clips else 'crossfade',
            transition_duration=0.3,
            transition_clips=transition_clips or None,
            lut_path=lut_path,
            bgm_path=bgm_path,
            bgm_volume=self.bgm_config.get('volume', 0.3),
        )

        meta['output'] = output_path
        meta['srt'] = srt_path
        meta['lut'] = lut_path
        meta['bgm'] = bgm_path
        meta['status'] = 'completed'

        meta_path = os.path.join(task_dir, 'meta.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(f'\n=== 完成 ===')
        print(f'  成片: {output_path}')
        print(f'  元数据: {meta_path}')
        return meta

    @staticmethod
    def _default_review(score=7, feedback='Review unavailable, auto-pass'):
        return {'score': score, 'dimensions': {}, 'feedback': feedback, 'pass': score >= 7}

    def _safe_review_character(self, ref_path, preview_paths):
        try:
            return self.llm.review_character(ref_path, preview_paths)
        except Exception as e:
            print(f'  ⚠ review_character failed: {e}')
            return self._default_review(score=5, feedback=f'Review error: {e}')

    def _safe_review_shot(self, scene_desc, frame_paths, char_ref=None):
        try:
            return self.llm.review_shot(scene_desc, frame_paths, character_ref=char_ref)
        except Exception as e:
            print(f'  ⚠ review_shot failed: {e}')
            return self._default_review(score=7, feedback=f'Review error: {e}')

    def _build_character_anchor(self, character_prompt: str, char_dir: str,
                                task_id: str, mode: str = 'auto') -> dict:
        """角色锚阶段

        mode='flux': 只生成 FLUX 角色参考图（不重建 3D）
        mode='3dgs'/'auto': FLUX生图 → TripoSplat→3DGS → 多角度预览审查
        """
        # [3a] FLUX 生成角色参考图
        print('  [3a] FLUX 生成角色参考图...')
        ref_path = os.path.join(char_dir, 'character_ref.png')
        char_size = self.flux_defaults.get('character_size', 1024)
        self.flux.generate_flux_t2i(
            prompt=character_prompt,
            width=char_size, height=char_size,
            steps=self.flux_defaults.get('steps', 20),
            guidance=self.flux_defaults.get('guidance', 3.5),
            filename_prefix=f'{task_id}/character_ref',
            output_path=ref_path,
        )
        print(f'  ref_image: {ref_path}')

        if mode == 'flux':
            return {
                'ref_image': ref_path,
                'mode': 'flux',
                'review': {'score': 10, 'pass': True, 'feedback': 'flux mode, no 3DGS'},
            }

        # [3b] TripoSplat 单图 → 3DGS (.ply)
        print('  [3b] TripoSplat → 3DGS...')
        ply_path = os.path.join(char_dir, 'character.ply')
        self.flux.generate_tripsplat(
            image_path=ref_path,
            filename_prefix=f'{task_id}/character',
            output_path=ply_path,
        )
        print(f'  ply: {ply_path}')

        # [3c] 多角度预览审查
        print('  [3c] 渲染多角度预览...')
        preview_dir = os.path.join(char_dir, 'previews')
        preview_paths = render_splat_angles(
            ply_path, angles=4, output_dir=preview_dir,
            size=512, pitch=15.0,
        )
        print(f'  previews: {len(preview_paths)} angles')

        review = self._safe_review_character(ref_path, preview_paths)
        print(f'  review: score={review["score"]}, pass={review["pass"]}')
        if not review['pass']:
            print(f'    feedback: {review["feedback"][:80]}')

        return {
            'ref_image': ref_path,
            'ply_path': ply_path,
            'preview_angles': preview_paths,
            'review': review,
            'mode': '3dgs',
        }

    def _generate_scene_ref(self, shot: dict, character: dict, clips_dir: str,
                            task_id: str, i2v_w: int, i2v_h: int,
                            bg_w: int, bg_h: int) -> str:
        """生成镜头参考帧（FLUX 场景图 或 3DGS 合成图）。可并行调用。"""
        sid = shot['id']
        camera = shot.get('camera', {})
        yaw = camera.get('yaw', 0)
        pitch = camera.get('pitch', 15)
        fov = camera.get('fov', 35)

        char_mode = character.get('mode', '3dgs')
        use_flux_ref = (char_mode == 'flux')

        if use_flux_ref:
            scene_prompt = self.llm.optimize_scene_prompt(
                character['desc'], shot['scene_desc'], shot.get('style', '')
            )
            composite_ref = os.path.join(clips_dir, f'composite_ref_{sid}.png')
            self.flux.generate_flux_t2i(
                prompt=scene_prompt,
                seed=random.randint(0, 2**31 - 1),
                width=i2v_w, height=i2v_h,
                steps=self.flux_defaults.get('steps', 20),
                guidance=self.flux_defaults.get('guidance', 3.5),
                filename_prefix=f'{task_id}/sceneref_{sid}',
                output_path=composite_ref,
            )
            print(f'  [prefetch] scene_ref (flux, yaw={yaw}): {sid}')
            return composite_ref
        else:
            bg_path = os.path.join(clips_dir, f'background_{sid}.png')
            self.flux.generate_flux_t2i(
                prompt=shot['background_prompt'],
                seed=random.randint(0, 2**31 - 1),
                width=bg_w, height=bg_h,
                steps=self.flux_defaults.get('steps', 20),
                guidance=self.flux_defaults.get('guidance', 3.5),
                filename_prefix=f'{task_id}/bg_{sid}',
                output_path=bg_path,
            )
            composite_ref = os.path.join(clips_dir, f'composite_ref_{sid}.png')
            render_splat_at_angle(
                ply_path=character['ply_path'],
                yaw=yaw, pitch=pitch,
                output_path=composite_ref,
                bg_image=bg_path,
                width=i2v_w, height=i2v_h,
                fov=fov,
            )
            print(f'  [prefetch] composite_ref (3dgs, yaw={yaw}): {sid}')
            return composite_ref

    def _generate_tts(self, shot: dict, clips_dir: str, voice: str) -> dict:
        """生成镜头配音。可并行调用。"""
        sid = shot['id']
        audio_path = os.path.join(clips_dir, f'shot_{sid}.wav')
        tts_result = self.tts.synthesize(
            text=shot['narration'],
            output_path=audio_path,
            voice=voice,
        )
        print(f'  [prefetch] tts: shot {sid} ({tts_result["duration"]:.1f}s)')
        return {
            'audio_path': audio_path,
            'duration': tts_result['duration'],
            'timestamps': tts_result['timestamps'],
        }

    def _process_shot(self, shot: dict, clips_dir: str, task_id: str,
                      character: dict = None, voice: str = None,
                      prefetched: dict = None) -> dict:
        """处理单个镜头：背景生成 → 合成参考帧 → I2V + TTS + 抽帧 + 审片 + 重试

        Args:
            prefetched: 预取数据 {'composite_ref': str, 'audio': dict} 或 None
        """
        sid = shot['id']
        duration = shot.get('duration', 5)
        fps = self.i2v_defaults.get('fps', 24)
        length = int(duration * fps)
        i2v_w = self.i2v_defaults.get('width', 832)
        i2v_h = self.i2v_defaults.get('height', 480)
        bg_w = self.flux_defaults.get('background_width', i2v_w)
        bg_h = self.flux_defaults.get('background_height', i2v_h)

        camera = shot.get('camera', {})

        attempts = []
        best = None

        for attempt in range(1, self.max_retries + 2):
            seed = random.randint(0, 2**32 - 1)
            video_prefix = f'{task_id}/shot_{sid}'
            video_path = os.path.join(clips_dir, f'shot_{sid}.mp4')
            if attempt > 1:
                video_path = os.path.join(clips_dir, f'shot_{sid}_v{attempt}.mp4')

            print(f'  attempt {attempt}, seed={seed}, length={length}')

            # 参考帧生成（首次尝试用预取数据，重试时重新生成）
            composite_ref = None
            if character:
                if attempt == 1 and prefetched and prefetched.get('composite_ref'):
                    composite_ref = prefetched['composite_ref']
                    print(f'  [prefetched] composite_ref: {composite_ref}')
                else:
                    composite_ref = self._generate_scene_ref(
                        shot, character, clips_dir, task_id,
                        i2v_w, i2v_h, bg_w, bg_h,
                    )

            # [8] Wan I2V (或 T2V fallback)
            if composite_ref:
                t2v_result = self.wan.generate_i2v(
                    prompt=shot['video_prompt'],
                    image_path=composite_ref,
                    seed=seed,
                    width=i2v_w, height=i2v_h,
                    length=length,
                    steps=self.i2v_defaults.get('steps', 20),
                    cfg=self.i2v_defaults.get('cfg', 5.0),
                    fps=fps,
                    filename_prefix=video_prefix,
                    output_path=video_path,
                )
            else:
                t2v_result = self.wan.generate_t2v(
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

            # [9] TTS（首次尝试用预取数据）
            if attempt == 1 and prefetched and prefetched.get('audio'):
                audio_info = prefetched['audio']
                audio_path = audio_info['audio_path']
                tts_result = {
                    'duration': audio_info['duration'],
                    'timestamps': audio_info['timestamps'],
                }
                print(f'  [prefetched] audio: {audio_path} ({tts_result["duration"]:.1f}s)')
            else:
                audio_path = os.path.join(clips_dir, f'shot_{sid}.wav')
                tts_result = self.tts.synthesize(
                    text=shot['narration'],
                    output_path=audio_path,
                    voice=voice,
                )
                print(f'  audio: {audio_path} ({tts_result["duration"]:.1f}s)')

            # 抽帧
            frames = extract_frames(video_path, n_frames=2,
                                    output_dir=os.path.join(clips_dir, 'frames'))
            print(f'  frames: {len(frames)}')

            # 审片
            char_ref = character['ref_image'] if character else None
            review = self._safe_review_shot(shot['scene_desc'], frames,
                                            char_ref=char_ref)
            print(f'  review: score={review["score"]}, pass={review["pass"]}')

            attempt_info = {
                'attempt': attempt,
                'seed': seed,
                'video': video_path,
                'composite_ref': composite_ref,
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
            'camera': camera,
            'attempts': attempts,
            'final_video': best['video'],
            'composite_ref': best.get('composite_ref'),
            'final_score': best['review']['score'],
            'audio': {
                'narration': shot['narration'],
                'audio_path': audio_path,
                'duration': tts_result['duration'],
                'timestamps': tts_result['timestamps'],
            },
        }


    def _prefetch_shots(self, shots: list, character: dict, clips_dir: str,
                        task_id: str, voice: str,
                        i2v_w: int, i2v_h: int, bg_w: int, bg_h: int) -> dict:
        """并行预取所有镜头的 FLUX 参考帧 + TTS 配音。

        FLUX 在 GPU0:8192，TTS 在 9880，与 Wan I2V (GPU2:8189) 独立。
        使用 ThreadPoolExecutor 并行提交，结果按 shot id 索引返回。

        Returns:
            {shot_id: {'composite_ref': str, 'audio': dict}}
        """
        if not character:
            # v0 纯 T2V：只预取 TTS
            results = {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for shot in shots:
                    futures[pool.submit(self._generate_tts, shot, clips_dir, voice)] = shot['id']
                for fut in as_completed(futures):
                    sid = futures[fut]
                    try:
                        results[sid] = {'audio': fut.result()}
                    except Exception as e:
                        print(f'  [prefetch] tts failed for shot {sid}: {e}')
                        results[sid] = {}
            return results

        results = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            for shot in shots:
                sid = shot['id']
                futures[pool.submit(self._generate_scene_ref, shot, character,
                                    clips_dir, task_id, i2v_w, i2v_h, bg_w, bg_h)] = ('ref', sid)
                futures[pool.submit(self._generate_tts, shot, clips_dir, voice)] = ('tts', sid)

            for fut in as_completed(futures):
                kind, sid = futures[fut]
                if sid not in results:
                    results[sid] = {}
                try:
                    if kind == 'ref':
                        results[sid]['composite_ref'] = fut.result()
                    else:
                        results[sid]['audio'] = fut.result()
                except Exception as e:
                    print(f'  [prefetch] {kind} failed for shot {sid}: {e}')

        print(f'  [prefetch] done: {len(results)} shots')
        return results

    def _select_lut(self, concept: str, script: dict, task_dir: str,
                    override: str = None) -> str:
        """选择调色 LUT 文件路径。

        Args:
            concept: 用户概念
            script: 编剧脚本
            override: 手动指定风格名（None 则 LLM 自动选择）

        Returns:
            LUT .cube 文件路径，或 None
        """
        lut_dir = self.color_config.get('lut_dir',
                                         os.path.join(os.path.dirname(__file__), '..', 'utils', 'luts'))
        styles = self.color_config.get('styles', ['cinematic'])

        if override:
            style = override
        else:
            try:
                style = self.llm.select_lut(concept, script, styles)
            except Exception as e:
                print(f'  ⚠ LUT selection failed: {e}, using default')
                style = self.color_config.get('default_style', styles[0])

        lut_path = os.path.join(lut_dir, f'{style}.cube')
        if not os.path.isfile(lut_path):
            print(f'  ⚠ LUT file not found: {lut_path}, skipping color grading')
            return None

        return lut_path

    def _generate_transitions(self, clips: list, clips_dir: str, task_id: str) -> list:
        """生成镜头间 RIFE 过渡视频。

        Args:
            clips: 视频片段路径列表
            clips_dir: 片段目录
            task_id: 任务 ID

        Returns:
            过渡视频路径列表（长度 = len(clips) - 1）
        """
        model = self.rife_config.get('model', 'rife_v4.26.safetensors')
        multiplier = self.rife_config.get('multiplier', 8)
        fps = self.rife_config.get('fps', 24)

        try:
            client = RIFEClient(config=self.config, instance='wan', model_name=model)
        except Exception as e:
            print(f'  ⚠ RIFE client init failed: {e}, skipping transitions')
            return []

        transitions = []
        trans_dir = os.path.join(clips_dir, 'transitions')
        os.makedirs(trans_dir, exist_ok=True)

        for i in range(len(clips) - 1):
            clip_a = clips[i]
            clip_b = clips[i + 1]
            print(f'  transition {i+1}→{i+2}: ...', end=' ', flush=True)

            try:
                n_a = get_video_frame_count(clip_a)
                n_b = get_video_frame_count(clip_b)
                frame_a = os.path.join(trans_dir, f'trans_{i}_frameA.png')
                frame_b = os.path.join(trans_dir, f'trans_{i}_frameB.png')
                rife_extract_frame(clip_a, max(0, n_a - 1), frame_a)
                rife_extract_frame(clip_b, 0, frame_b)

                output = os.path.join(trans_dir, f'trans_{i}.mp4')
                result = client.interpolate_transition(
                    frame_a, frame_b, output,
                    multiplier=multiplier, fps=fps,
                    filename_prefix=f'vidance/{task_id}/trans_{i}',
                )
                print(f'{result["frame_count"]} frames')
                transitions.append(output)
            except Exception as e:
                print(f'failed: {e}')
                transitions.append(None)

        return transitions


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Vidance v1 pipeline')
    parser.add_argument('concept', help='Video concept in Chinese')
    parser.add_argument('-o', '--output', default=None, help='Output video path')
    parser.add_argument('--character', default=None,
                        help='Character description in Chinese (e.g. 穿红斗篷的少年)')
    parser.add_argument('--character-mode', default='auto', choices=['auto', '3dgs', 'flux'],
                        help='Character anchor mode: auto(3DGS→flux降级) / 3dgs / flux (default: auto)')
    parser.add_argument('--voice', default=None,
                        help='TTS voice (e.g. edge-moe, cosy-default)')
    parser.add_argument('--no-rife', action='store_true',
                        help='Disable RIFE frame interpolation transitions')
    parser.add_argument('--lut', default=None,
                        choices=['cinematic', 'warm', 'cool', 'vintage', 'vivid', 'soft'],
                        help='Color grading LUT style (auto-select if not specified)')
    parser.add_argument('--no-color', action='store_true',
                        help='Disable color grading')
    args = parser.parse_args()

    pipeline = Pipeline()
    if args.no_rife:
        pipeline.rife_config['enabled'] = False
    if args.no_color:
        pipeline.color_config['enabled'] = False
    meta = pipeline.run(args.concept, output_path=args.output,
                        character_desc=args.character, voice=args.voice,
                        character_mode=args.character_mode,
                        lut_override=args.lut)
    print(f'\nDone: {meta["output"]}')
