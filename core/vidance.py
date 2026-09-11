#!/usr/bin/env python3
"""Vidance 统一入口 — AI 有声短片生成

子命令:
  auto    概念 → LLM 编剧 → Wan/FLUX 生成 → 后处理（RIFE/LUT/BGM/STT）
  custom  参考图 + 预写脚本 → H3 ref2va 生成 → 后处理
  quick   纯 T2V，无角色锚无后处理

用法:
  python core/vidance.py auto "雪山日出：小狐狸的冒险" --character "红色小狐狸" --character-mode flux --stt
  python core/vidance.py custom --ref input/doubao.jpg --ref input/naiwa.jpg --script input/prompt1.txt --lut cinematic --bgm dramatic
  python core/vidance.py quick "一只猫在月球上跳舞"
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import Pipeline
from core.custom_gen import run_custom


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

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
