#!/usr/bin/env python3
"""ffmpeg + moviepy 后处理工具 — 抽帧、SRT生成、视频合成

用法:
  from utils.ffmpeg_tools import extract_frames, generate_srt, compose
  extract_frames('clips/shot_1.mp4', 4, 'clips/frames/')
  generate_srt(timestamps, 'clips/subtitle.srt')
  compose(clips, audio_path, srt_path, 'output/final.mp4')
"""
import os
import sys
import json
import subprocess
import shutil

FFMPEG = shutil.which('ffmpeg') or os.path.expanduser('~/.local/bin/ffmpeg')


def _run(cmd: list, timeout: int = 300):
    """运行命令，检查返回码"""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f'command failed: {" ".join(cmd[:5])}...\nstderr: {result.stderr[-500:]}')
    return result


def get_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    cmd = [
        FFMPEG, '-i', video_path,
        '-show_entries', 'format=duration',
        '-v', 'quiet',
        '-of', 'csv=p=0',
    ]
    # ffprobe is more reliable for this, but use ffmpeg -i fallback
    cmd[0] = shutil.which('ffprobe') or FFMPEG.replace('ffmpeg', 'ffprobe')
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(video_path: str, n_frames: int = 4,
                   output_dir: str = None, prefix: str = None) -> list:
    """从视频中均匀抽取 n_frames 帧，返回帧文件路径列表"""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(video_path), 'frames')
    os.makedirs(output_dir, exist_ok=True)
    if prefix is None:
        prefix = os.path.splitext(os.path.basename(video_path))[0]

    duration = get_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f'cannot get duration for {video_path}')

    frame_paths = []
    for i in range(n_frames):
        t = duration * (i + 0.5) / n_frames
        out_path = os.path.join(output_dir, f'{prefix}_frame_{i}.jpg')
        cmd = [
            FFMPEG, '-y', '-ss', f'{t:.2f}', '-i', video_path,
            '-frames:v', '1', '-q:v', '2', out_path,
        ]
        _run(cmd, timeout=30)
        if os.path.isfile(out_path):
            frame_paths.append(out_path)
    return frame_paths


def _srt_time(seconds: float) -> str:
    """秒→SRT时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def generate_srt(timestamps: list, output_path: str, offset: float = 0.0) -> str:
    """从 TTS 时间戳生成 SRT 字幕文件

    Args:
        timestamps: [{text, start, end, duration, ...}, ...]
        output_path: SRT 输出路径
        offset: 时间偏移（秒，用于拼接时各镜头的起始偏移）
    """
    lines = []
    for i, ts in enumerate(timestamps):
        idx = i + 1
        start = ts['start'] + offset
        end = ts['end'] + offset
        text = ts['text']
        lines.append(str(idx))
        lines.append(f'{_srt_time(start)} --> {_srt_time(end)}')
        lines.append(text)
        lines.append('')

    srt_content = '\n'.join(lines) + '\n'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(srt_content)
    return output_path


def compose(clips: list, audio_paths: list, srt_path: str = None,
            output_path: str = 'output/final.mp4',
            transition: str = 'crossfade',
            transition_duration: float = 0.3) -> str:
    """合成最终视频：拼接画面 + 合并配音 + 烧录字幕

    Args:
        clips: 视频片段路径列表 [shot_1.mp4, shot_2.mp4, ...]
        audio_paths: 对应音频路径列表 [shot_1.wav, shot_2.wav, ...]
        srt_path: SRT字幕文件路径（None则不烧字幕）
        output_path: 输出视频路径
        transition: 转场类型 ('crossfade' 或 'cut')
        transition_duration: 转场时长（秒）
    """
    from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips
    from moviepy.video.fx import CrossFadeIn, Freeze

    # 加载各片段，按音频时长对齐
    video_clips = []
    all_audio_clips = []

    for i, (video_path, audio_path) in enumerate(zip(clips, audio_paths)):
        vc = VideoFileClip(video_path)
        ac = AudioFileClip(audio_path)

        # 以音频时长为准：画面不足则定格末帧，画面多余则截断
        if ac.duration > vc.duration:
            freeze_t = max(0, vc.duration - 0.04)
            vc = vc.with_effects([Freeze(t=freeze_t,
                                         freeze_duration=ac.duration - vc.duration,
                                         padding_end=True)])
        elif ac.duration < vc.duration:
            vc = vc.subclipped(0, ac.duration)

        vc = vc.with_audio(ac)

        if transition == 'crossfade' and i > 0:
            vc = vc.with_effects([CrossFadeIn(transition_duration)])

        video_clips.append(vc)
        all_audio_clips.append(ac)

    if transition == 'crossfade' and len(video_clips) > 1:
        final_video = concatenate_videoclips(
            video_clips,
            method='compose',
            padding=-transition_duration,
        )
    else:
        final_video = concatenate_videoclips(video_clips, method='compose')

    # 先渲染无字幕版本
    temp_output = output_path.replace('.mp4', '_nosub.mp4')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    final_video.write_videofile(
        temp_output,
        codec='libx264',
        audio_codec='aac',
        fps=24,
        logger=None,
    )
    final_video.close()
    for vc in video_clips:
        vc.close()
    for ac in all_audio_clips:
        ac.close()

    # 烧录字幕
    if srt_path and os.path.isfile(srt_path):
        subtitle_filter = f"subtitles='{srt_path}':force_style='FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2'"
        cmd = [
            FFMPEG, '-y', '-i', temp_output,
            '-vf', subtitle_filter,
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            output_path,
        ]
        _run(cmd, timeout=600)
        os.remove(temp_output)
    else:
        shutil.move(temp_output, output_path)

    return output_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ffmpeg tools')
    sub = parser.add_subparsers(dest='cmd')

    p_frames = sub.add_parser('frames', help='Extract frames')
    p_frames.add_argument('video')
    p_frames.add_argument('-n', '--count', type=int, default=4)
    p_frames.add_argument('-o', '--output', default=None)

    p_srt = sub.add_parser('srt', help='Generate SRT from timestamps JSON')
    p_srt.add_argument('timestamps_json', help='JSON file with timestamps')
    p_srt.add_argument('-o', '--output', required=True)

    args = parser.parse_args()

    if args.cmd == 'frames':
        paths = extract_frames(args.video, args.count, args.output)
        print(f'Extracted {len(paths)} frames:')
        for p in paths:
            print(f'  {p}')
    elif args.cmd == 'srt':
        with open(args.timestamps_json) as f:
            ts = json.load(f)
        generate_srt(ts, args.output)
        print(f'Generated: {args.output}')
