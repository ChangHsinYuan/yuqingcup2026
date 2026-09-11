#!/usr/bin/env python3
"""Vidance 后处理模块 — RIFE 过渡 / LUT 调色 / BGM ducking / STT 字幕

被 pipeline.py（auto 模式）和 custom_gen.py（custom 模式）共享调用。
所有方法均可独立使用，不依赖 moviepy 合成流程。
"""
import os
import sys
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rife import RIFEClient, extract_frame as rife_extract_frame, get_video_frame_count
from utils.music import generate_bgm
from utils.stt import transcribe_audio
from utils.ffmpeg_tools import generate_srt

FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'


class PostProcessor:
    """共享后处理器：RIFE 过渡、LUT 调色、BGM ducking、STT 字幕。"""

    def __init__(self, config: dict, llm=None):
        self.config = config
        self.llm = llm
        self.rife_config = config.get('rife', {})
        self.color_config = config.get('color', {})
        self.bgm_config = config.get('bgm', {})
        self.stt_config = config.get('stt', {})

    # ── RIFE 过渡 ──

    def generate_transitions(self, clips: list, clips_dir: str, task_id: str,
                             transition_types: list = None) -> list:
        """生成镜头间 RIFE 过渡视频。

        Args:
            clips: 视频片段路径列表
            clips_dir: 片段目录
            task_id: 任务 ID
            transition_types: 每个边界的过渡类型列表 ['rife', 'crossfade', 'cut', ...]
                长度 = len(clips) - 1。None 则全部用 'rife'。
                只为 'rife' 类型生成过渡视频，其他类型返回 None。

        Returns:
            过渡视频路径列表（长度 = len(clips) - 1），'rife' 位置为路径或 None，
            非 'rife' 位置为 None
        """
        if not self.rife_config.get('enabled', False) or len(clips) < 2:
            return []

        n_trans = len(clips) - 1
        if transition_types is None:
            transition_types = ['rife'] * n_trans

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

        for i in range(n_trans):
            trans_type = transition_types[i] if i < len(transition_types) else 'rife'

            if trans_type != 'rife':
                print(f'  transition {i+1}→{i+2}: skipped ({trans_type})')
                transitions.append(None)
                continue

            clip_a = clips[i]
            clip_b = clips[i + 1]
            print(f'  transition {i+1}→{i+2} (rife): ...', end=' ', flush=True)

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

    # ── LUT 调色 ──

    def select_lut(self, concept: str, script: dict, task_dir: str,
                   override: str = None) -> str:
        """选择调色 LUT 文件路径。

        Args:
            concept: 用户概念
            script: 编剧脚本
            task_dir: 任务目录（未使用，保留兼容）
            override: 手动指定风格名（None 则 LLM 自动选择）

        Returns:
            LUT .cube 文件路径，或 None
        """
        lut_dir = self.color_config.get('lut_dir',
                                         os.path.join(os.path.dirname(__file__), '..', 'utils', 'luts'))
        styles = self.color_config.get('styles', ['cinematic'])

        if override:
            style = override
        elif self.llm:
            try:
                style = self.llm.select_lut(concept, script, styles)
            except Exception as e:
                print(f'  ⚠ LUT selection failed: {e}, using default')
                style = self.color_config.get('default_style', styles[0])
        else:
            style = self.color_config.get('default_style', styles[0])

        lut_path = os.path.join(lut_dir, f'{style}.cube')
        if not os.path.isfile(lut_path):
            print(f'  ⚠ LUT file not found: {lut_path}, skipping color grading')
            return None

        return lut_path

    def apply_lut(self, video_path: str, lut_path: str) -> bool:
        """对成品视频施加 LUT 调色（就地替换）。

        Args:
            video_path: 视频文件路径（将被替换）
            lut_path: 3D LUT .cube 文件路径

        Returns:
            True 成功，False 失败
        """
        if not lut_path or not os.path.isfile(lut_path):
            return False

        temp = video_path.replace('.mp4', '_lut.mp4')
        shutil.move(video_path, temp)
        cmd = [
            FFMPEG, '-y', '-i', temp,
            '-vf', f"lut3d='{lut_path}'",
            '-c:a', 'copy',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
            '-pix_fmt', 'yuv420p',
            video_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=600)
            os.remove(temp)
            return os.path.isfile(video_path)
        except Exception as e:
            print(f'  ⚠ apply_lut failed: {e}')
            if os.path.isfile(temp):
                shutil.move(temp, video_path)
            return False

    # ── BGM 配乐 ──

    def select_bgm(self, concept: str, script: dict, task_dir: str,
                   duration: float, override: str = None) -> str:
        """选择或生成配乐 BGM。

        Args:
            concept: 用户概念
            script: 编剧脚本
            task_dir: 任务目录（BGM 输出到此）
            duration: BGM 时长（秒）
            override: 手动指定 mood 名（None 则 LLM 自动选择）

        Returns:
            BGM wav 文件路径，或 None
        """
        moods = self.bgm_config.get('moods', ['calm'])

        if override:
            mood = override
        elif self.llm:
            try:
                mood = self.llm.select_bgm_mood(concept, script, moods)
            except Exception as e:
                print(f'  ⚠ BGM mood selection failed: {e}, using default')
                mood = self.bgm_config.get('default_mood', moods[0])
        else:
            mood = self.bgm_config.get('default_mood', moods[0])

        # 优先使用自定义 BGM 目录
        custom_dir = self.bgm_config.get('custom_dir', '')
        if custom_dir and os.path.isdir(custom_dir):
            custom_path = os.path.join(custom_dir, f'{mood}.wav')
            if os.path.isfile(custom_path):
                print(f'  [custom] using {custom_path}')
                return custom_path

        # 生成 BGM
        bgm_path = os.path.join(task_dir, 'bgm.wav')
        try:
            generate_bgm(mood, duration, bgm_path)
            print(f'  [generated] {mood}, {duration:.1f}s')
            return bgm_path
        except Exception as e:
            print(f'  ⚠ BGM generation failed: {e}')
            return None

    def add_bgm(self, video_path: str, bgm_path: str,
                volume: float = None) -> bool:
        """对成品视频添加 BGM ducking 混音（就地替换）。

        Args:
            video_path: 视频文件路径（将被替换）
            bgm_path: BGM wav 文件路径
            volume: BGM 基础音量 (0-1)，默认用 config 值

        Returns:
            True 成功，False 失败
        """
        if not bgm_path or not os.path.isfile(bgm_path):
            return False

        if volume is None:
            volume = self.bgm_config.get('volume', 0.3)

        temp = video_path.replace('.mp4', '_nobgm.mp4')
        shutil.move(video_path, temp)
        cmd = [
            FFMPEG, '-y', '-i', temp, '-i', bgm_path,
            '-filter_complex',
            f'[1:a]volume={volume}[bgm];'
            f'[bgm][0:a]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=300[ducked];'
            f'[0:a][ducked]amix=inputs=2:duration=first:weights=1 0.8[a]',
            '-map', '0:v', '-map', '[a]',
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            video_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=600)
            os.remove(temp)
            return os.path.isfile(video_path)
        except Exception as e:
            print(f'  ⚠ add_bgm failed: {e}')
            if os.path.isfile(temp):
                shutil.move(temp, video_path)
            return False

    # ── STT 字幕 ──

    def burn_subtitles(self, video_path: str, srt_path: str) -> bool:
        """将 SRT 字幕烧录进视频（就地替换）。返回 True 成功。"""
        if not srt_path or not os.path.isfile(srt_path):
            return False

        temp = video_path.replace('.mp4', '_resub.mp4')
        shutil.move(video_path, temp)
        subtitle_filter = (
            f"subtitles='{srt_path}':force_style='FontSize=24,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=2,Alignment=2'"
        )
        try:
            subprocess.run(
                [FFMPEG, '-y', '-i', temp, '-vf', subtitle_filter,
                 '-c:a', 'copy', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                 '-pix_fmt', 'yuv420p',
                 video_path],
                capture_output=True, timeout=600,
            )
            os.remove(temp)
            return os.path.isfile(video_path)
        except Exception as e:
            print(f'  ⚠ burn_subtitles failed: {e}')
            if os.path.isfile(temp):
                shutil.move(temp, video_path)
            return False

    def run_stt(self, video_path: str, task_dir: str) -> str:
        """用 faster-whisper 对成片音频做 STT 转写，生成 SRT 并烧录字幕。

        Args:
            video_path: 成片视频路径
            task_dir: 任务目录

        Returns:
            STT SRT 文件路径，或 None
        """
        # 提取成片音频
        audio_path = os.path.join(task_dir, 'final_audio.wav')
        try:
            subprocess.run(
                [FFMPEG, '-y', '-i', video_path,
                 '-vn', '-ac', '1', '-ar', '16000', '-q:a', '2', audio_path],
                capture_output=True, timeout=120,
            )
            if not os.path.isfile(audio_path):
                print('  ⚠ STT: failed to extract audio')
                return None
        except Exception as e:
            print(f'  ⚠ STT: audio extraction failed: {e}')
            return None

        # STT 转写
        language = self.stt_config.get('language', 'zh')
        try:
            segments = transcribe_audio(audio_path, language=language, config=self.config)
            print(f'  STT: {len(segments)} segments')
            for s in segments[:3]:
                print(f'    [{s["start"]:.2f}-{s["end"]:.2f}] {s["text"]}')
            if len(segments) > 3:
                print(f'    ... ({len(segments) - 3} more)')
        except Exception as e:
            print(f'  ⚠ STT: transcription failed: {e}')
            return None

        # 生成 SRT
        srt_path = os.path.join(task_dir, 'subtitle_stt.srt')
        generate_srt(segments, srt_path)

        # 烧录 STT 字幕
        if self.burn_subtitles(video_path, srt_path):
            return srt_path
        return None

    # ── 一键后处理 ──

    def run_all(self, video_path: str, clips: list, clips_dir: str, task_id: str,
                task_dir: str, concept: str = '', script: dict = None,
                duration: float = 0, srt_path: str = None,
                no_rife: bool = False, lut_override: str = None,
                no_color: bool = False, bgm_override: str = None,
                no_bgm: bool = False, use_stt: bool = False,
                transition_types: list = None) -> dict:
        """一键执行全部后处理（RIFE→LUT→BGM→STT）。

        用于 custom 模式：concat 之后逐步施加。
        auto 模式在 compose() 内部已完成 LUT/BGM/字幕，仅单独调用 RIFE 和 STT。

        Returns:
            {'lut': str, 'bgm': str, 'srt': str, 'transitions': list}
        """
        result = {'lut': None, 'bgm': None, 'srt': srt_path, 'transitions': []}

        # RIFE 过渡（需要在 concat 之前插入，这里只生成过渡片段）
        if not no_rife and self.rife_config.get('enabled', False) and len(clips) >= 2:
            print(f'\n=== [post] RIFE 过渡 ===')
            result['transitions'] = self.generate_transitions(
                clips, clips_dir, task_id, transition_types=transition_types)

        # LUT 调色
        if not no_color and (self.color_config.get('enabled', False) or lut_override):
            print(f'\n=== [post] LUT 调色 ===')
            lut_path = self.select_lut(concept, script or {}, task_dir, lut_override)
            if lut_path:
                print(f'  LUT: {os.path.basename(lut_path)}')
                if self.apply_lut(video_path, lut_path):
                    result['lut'] = lut_path

        # BGM 配乐
        if not no_bgm and (self.bgm_config.get('enabled', False) or bgm_override):
            print(f'\n=== [post] BGM 配乐 ===')
            if duration <= 0:
                # 从成品视频探测时长
                dur = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'csv=p=0', video_path],
                    capture_output=True, text=True).stdout.strip()
                duration = float(dur) if dur else 10.0
            bgm_path = self.select_bgm(concept, script or {}, task_dir,
                                       duration + 2.0, bgm_override)
            if bgm_path:
                print(f'  BGM: {os.path.basename(bgm_path)}')
                if self.add_bgm(video_path, bgm_path):
                    result['bgm'] = bgm_path

        # STT 字幕
        if use_stt or self.stt_config.get('enabled', False):
            print(f'\n=== [post] STT 字幕 ===')
            stt_srt = self.run_stt(video_path, task_dir)
            if stt_srt:
                result['srt'] = stt_srt
                print(f'  STT SRT: {stt_srt}')
            elif srt_path:
                print(f'  ⚠ STT 失败，回退烧录已有字幕: {srt_path}')
                self.burn_subtitles(video_path, srt_path)

        return result
