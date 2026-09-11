#!/usr/bin/env python3
"""Vidance 统一入口 — AI 有声短片生成

子命令:
  auto    概念 → LLM 编剧 → Wan/FLUX 生成 → 后处理（RIFE/LUT/BGM/STT）
  custom  参考图 + 预写脚本 → H3 ref2va 生成 → 后处理
  quick   纯 T2V，无角色锚无后处理
  concat  多视频拼接（硬切 / RIFE 过渡 / 交叉淡化）

用法:
  python core/vidance.py auto "雪山日出：小狐狸的冒险" --character "红色小狐狸" --character-mode flux --stt
  python core/vidance.py auto "深海探险" --character "蓝色水母" --duration 60 --slowmo 2
  python core/vidance.py custom --ref input/doubao.jpg --ref input/naiwa.jpg --script input/prompt1.txt --lut cinematic --bgm dramatic
  python core/vidance.py quick "一只猫在月球上跳舞"
  python core/vidance.py concat clip1.mp4 clip2.mp4 clip3.mp4 -o merged.mp4 --transition rife
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import Pipeline
from core.custom_gen import run_custom, concat_videos
from core.postprocess import PostProcessor


def cmd_auto(args):
    """auto 子命令：概念 → LLM 编剧 → 生成 → 后处理"""
    pipeline = Pipeline()
    if args.no_rife:
        pipeline.rife_config['enabled'] = False
    if args.no_color:
        pipeline.color_config['enabled'] = False
    if args.no_bgm:
        pipeline.bgm_config['enabled'] = False

    meta = pipeline.run(
        args.concept,
        output_path=args.output,
        character_desc=args.character,
        voice=args.voice,
        character_mode=args.character_mode,
        lut_override=args.lut,
        bgm_override=args.bgm,
        use_stt=args.stt,
        target_duration=args.duration,
        slowmo_override=args.slowmo,
    )
    print(f'\nDone: {meta["output"]}')


def cmd_custom(args):
    """custom 子命令：参考图 + 脚本 → H3 ref2va → 后处理"""
    post_kwargs = dict(
        no_rife=args.no_rife,
        lut_override=args.lut,
        no_color=args.no_color,
        bgm_override=args.bgm,
        no_bgm=args.no_bgm,
        use_stt=args.stt,
    )
    meta = run_custom(
        ref_images=args.ref,
        script_path=args.script,
        output_path=args.output,
        ref_image_size=args.ref_image_size,
        post_kwargs=post_kwargs,
    )
    print(f'\nDone: {meta["output"]}')


def cmd_quick(args):
    """quick 子命令：纯 T2V，无角色无后处理"""
    pipeline = Pipeline()
    pipeline.rife_config['enabled'] = False
    pipeline.color_config['enabled'] = False
    pipeline.bgm_config['enabled'] = False

    meta = pipeline.run(
        args.concept,
        output_path=args.output,
        character_desc=None,
        voice=args.voice,
        character_mode='auto',
        lut_override=None,
        bgm_override=None,
        use_stt=False,
    )
    print(f'\nDone: {meta["output"]}')


def _concat_cut(clips, output_path):
    """硬切拼接（ffmpeg concat demuxer, stream copy）"""
    list_path = output_path.replace('.mp4', '_concat.txt')
    with open(list_path, 'w') as f:
        for vp in clips:
            f.write(f"file '{os.path.abspath(vp)}'\n")
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
        '-c', 'copy', output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(list_path)
    return output_path


def _concat_crossfade(clips, output_path, duration=0.5):
    """交叉淡化拼接（moviepy）"""
    from moviepy import VideoFileClip, concatenate_videoclips
    from moviepy.video.fx import CrossFadeIn

    video_clips = []
    for i, vp in enumerate(clips):
        vc = VideoFileClip(vp)
        if i > 0:
            vc = vc.with_effects([CrossFadeIn(duration)])
        video_clips.append(vc)

    final = concatenate_videoclips(video_clips, method='compose')
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    final.write_videofile(output_path, codec='libx264', audio_codec='aac',
                          fps=24, logger=None)
    final.close()
    for vc in video_clips:
        vc.close()
    return output_path


def cmd_concat(args):
    """concat 子命令：多视频拼接"""
    clips = args.videos
    for vp in clips:
        if not os.path.isfile(vp):
            print(f'错误: 文件不存在: {vp}')
            sys.exit(1)

    output_path = args.output
    if output_path is None:
        output_path = os.path.join(os.path.dirname(clips[0]), 'merged.mp4')
    if not os.path.isabs(output_path):
        from core.pipeline import CONFIG_PATH
        config_path = CONFIG_PATH
        with open(config_path) as f:
            config = json.load(f)
        output_path = os.path.join(config['output_dir'], output_path)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    transition = args.transition
    print(f'拼接 {len(clips)} 个视频 → {output_path} (transition={transition})')

    if transition == 'cut':
        _concat_cut(clips, output_path)
    elif transition == 'rife':
        with open(os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')) as f:
            config = json.load(f)
        post = PostProcessor(config)
        tmpdir = tempfile.mkdtemp(prefix='vidance_concat_')
        try:
            transition_types = ['rife'] * (len(clips) - 1)
            transitions = post.generate_transitions(clips, tmpdir, 'concat',
                                                     transition_types=transition_types)
            concat_videos(clips, output_path, transitions)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    elif transition == 'crossfade':
        _concat_crossfade(clips, output_path, duration=args.crossfade_duration)

    dur = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', output_path],
        capture_output=True, text=True).stdout.strip()
    print(f'\nDone: {output_path} ({float(dur):.1f}s)')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='vidance',
        description='Vidance — AI 有声短片生成',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest='command', required=True, metavar='<command>')

    # ── 共享参数组 ──
    output_parent = argparse.ArgumentParser(add_help=False)
    output_parent.add_argument('-o', '--output', default=None,
                               help='输出视频路径（相对路径自动 resolve 到 output_dir）')

    post_parent = argparse.ArgumentParser(add_help=False)
    post_parent.add_argument('--no-rife', action='store_true',
                             help='禁用 RIFE 镜头间过渡')
    post_parent.add_argument('--lut', default=None,
                             choices=['cinematic', 'warm', 'cool', 'vintage', 'vivid', 'soft'],
                             help='调色 LUT 风格（不指定则 LLM 自动选择）')
    post_parent.add_argument('--no-color', action='store_true',
                             help='禁用 LUT 调色')
    post_parent.add_argument('--bgm', default=None,
                             choices=['calm', 'uplifting', 'mysterious', 'dramatic', 'playful'],
                             help='BGM mood（不指定则 LLM 自动选择）')
    post_parent.add_argument('--no-bgm', action='store_true',
                             help='禁用背景音乐')
    post_parent.add_argument('--stt', action='store_true',
                             help='使用 faster-whisper STT 字幕对齐（覆盖 TTS 时间戳）')

    # ── auto 子命令 ──
    p_auto = sub.add_parser('auto', parents=[output_parent, post_parent],
                            help='概念 → LLM 编剧 → Wan/FLUX 生成 → 后处理')
    p_auto.add_argument('concept', help='视频概念（中文）')
    p_auto.add_argument('--character', default=None,
                        help='角色描述（中文，如：穿红斗篷的少年）')
    p_auto.add_argument('--character-mode', default='auto',
                        choices=['auto', '3dgs', 'flux'],
                        help='角色锚模式: auto(3DGS→flux降级) / 3dgs / flux (默认: auto)')
    p_auto.add_argument('--voice', default=None,
                        help='TTS 音色 (如 edge-moe, cosy-default)')
    p_auto.add_argument('--duration', type=float, default=None,
                        help='目标总时长（秒），动态调整镜头数（解锁长片，不指定则默认 2-5 镜）')
    p_auto.add_argument('--slowmo', type=int, default=None,
                        help='全局慢动作帧倍率（如 2 = 2x 慢放，覆盖所有镜头）')
    p_auto.set_defaults(func=cmd_auto)

    # ── custom 子命令 ──
    p_custom = sub.add_parser('custom', parents=[output_parent, post_parent],
                              help='参考图 + 预写脚本 → H3 ref2va 生成 → 后处理')
    p_custom.add_argument('--ref', action='append', required=True,
                          help='角色参考图路径（可重复指定多个角色）')
    p_custom.add_argument('--script', required=True,
                          help='分镜脚本文件路径（prompt.txt 格式）')
    p_custom.add_argument('--ref-image-size', choices=['match', 'max'], default='match',
                          help='H3 参考图尺寸: match (更快) / max (2048px, 最佳身份保真)')
    p_custom.set_defaults(func=cmd_custom)

    # ── quick 子命令 ──
    p_quick = sub.add_parser('quick', parents=[output_parent],
                             help='纯 T2V，无角色锚无后处理')
    p_quick.add_argument('concept', help='视频概念（中文）')
    p_quick.add_argument('--voice', default=None,
                         help='TTS 音色 (如 edge-moe)')
    p_quick.set_defaults(func=cmd_quick)

    # ── concat 子命令 ──
    p_concat = sub.add_parser('concat', parents=[output_parent],
                              help='多视频拼接（硬切 / RIFE 过渡 / 交叉淡化）')
    p_concat.add_argument('videos', nargs='+',
                          help='待拼接的视频文件路径列表')
    p_concat.add_argument('--transition', default='cut',
                          choices=['cut', 'rife', 'crossfade'],
                          help='过渡方式: cut(硬切) / rife(光流插帧) / crossfade(交叉淡化) (默认: cut)')
    p_concat.add_argument('--crossfade-duration', type=float, default=0.5,
                          help='交叉淡化时长（秒，仅 --transition crossfade 时生效）')
    p_concat.set_defaults(func=cmd_concat)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
