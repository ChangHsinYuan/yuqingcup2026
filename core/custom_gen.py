#!/usr/bin/env python3
"""自定义脚本生成 — 用参考图 + 预写分镜脚本直接生成视频（内部模块）

推荐使用统一入口: python core/vidance.py custom ...

绕过 pipeline 的 LLM 编剧 + 单角色锚流程，直接：
  1. LLM 看角色三视图 → 角色描述
  2. 角色描述 + <Picture i> 引用 + 分镜脚本 → H3 ref2va 生成视频（带原生音频）
  3. RIFE 过渡（可选）→ ffmpeg 拼接 → LUT 调色 / BGM / STT（可选）
"""
import json
import os
import re
import sys
import time
import random
import subprocess
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm import LLMClient
from utils.comfy_api import ComfyClient
from core.postprocess import PostProcessor

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def parse_script(script_path: str) -> dict:
    """解析 prompt.txt 格式的分镜脚本

    格式：
      [全局场景设定行]
      [全局角色设定行]
      【XX｜标题｜约X秒】
      生成提示词：
      [镜头描述（可多行）]
      【XX｜标题｜约X秒】
      生成提示词：
      [镜头描述（可多行）]
      【剪辑与声音...】（忽略）

    返回:
      {
        'scene_setting': str,       # 全局场景设定
        'character_setting': str,   # 全局角色设定
        'shots': [
          {'id': '01A', 'title': '睡觉与算盘', 'duration': 3, 'desc': '固定中广景...'}
        ]
      }
    """
    with open(script_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    shot_marker = re.compile(r'【(.+?)｜(.+?)｜约([\d.]+)秒】')
    shots = []
    scene_setting = ''
    character_setting = ''
    current_shot = None
    in_desc = False
    pre_shot_lines = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        m = shot_marker.match(line_stripped)
        if m:
            if current_shot and in_desc:
                current_shot['desc'] = current_shot['desc'].strip()
                shots.append(current_shot)

            if '剪辑' in m.group(2) or '声音' in m.group(2):
                current_shot = None
                in_desc = False
                continue

            current_shot = {
                'id': m.group(1).strip(),
                'title': m.group(2).strip(),
                'duration': float(m.group(3)),
                'desc': '',
            }
            in_desc = False
            continue

        if line_stripped.startswith('【') and line_stripped.endswith('】'):
            if current_shot and in_desc:
                current_shot['desc'] = current_shot['desc'].strip()
                shots.append(current_shot)
            current_shot = None
            in_desc = False
            continue

        if line_stripped == '生成提示词：':
            in_desc = True
            continue

        if current_shot is None:
            if not line_stripped.startswith('生成提示词'):
                pre_shot_lines.append(line_stripped)
        elif in_desc:
            if current_shot['desc']:
                current_shot['desc'] += ' '
            current_shot['desc'] += line_stripped

    if current_shot and in_desc:
        current_shot['desc'] = current_shot['desc'].strip()
        shots.append(current_shot)

    if len(pre_shot_lines) >= 1:
        scene_setting = pre_shot_lines[0]
    if len(pre_shot_lines) >= 2:
        character_setting = pre_shot_lines[1]

    return {
        'scene_setting': scene_setting,
        'character_setting': character_setting,
        'shots': shots,
    }


def concat_videos(video_paths: list, output_path: str,
                  transition_clips: list = None) -> str:
    """ffmpeg 硬切拼接视频（保留音频）。

    Args:
        video_paths: 视频片段路径列表
        output_path: 输出路径
        transition_clips: 镜头间过渡视频路径列表（长度 = len(video_paths) - 1），
                          None 或空列表则直接拼接
    """
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    # 构建拼接列表：clip1, [trans1,] clip2, [trans2,] ...
    concat_list = []
    for i, vp in enumerate(video_paths):
        concat_list.append(vp)
        if transition_clips and i < len(transition_clips):
            tc = transition_clips[i]
            if tc and os.path.isfile(tc):
                concat_list.append(tc)

    if len(concat_list) == 1:
        shutil.copy2(concat_list[0], output_path)
        return output_path

    list_path = output_path.replace('.mp4', '_concat.txt')
    with open(list_path, 'w') as f:
        for vp in concat_list:
            abs_path = os.path.abspath(vp)
            f.write(f"file '{abs_path}'\n")

    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', list_path,
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-preset', 'fast', '-crf', '18',
        '-c:a', 'aac',
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(list_path)
    return output_path


def run_custom(ref_images: list, script_path: str,
               output_path: str = None, ref_image_size: str = 'match',
               post_kwargs: dict = None) -> dict:
    """自定义脚本生成入口（被 vidance.py custom 调用）。

    Args:
        ref_images: 角色参考图路径列表
        script_path: 分镜脚本文件路径
        output_path: 输出视频路径（None 则自动生成）
        ref_image_size: H3 参考图尺寸 'match' 或 'max'
        post_kwargs: 后处理参数 {
            no_rife, lut_override, no_color,
            bgm_override, no_bgm, use_stt
        }

    Returns:
        任务元数据 dict
    """
    if post_kwargs is None:
        post_kwargs = {}

    config = load_config()
    llm = LLMClient(config)
    h3 = ComfyClient(config=config, instance='h3')
    post = PostProcessor(config, llm=llm)

    output_dir = config['output_dir']
    task_id = time.strftime('%Y%m%d_%H%M%S')
    task_dir = os.path.join(output_dir, f'custom_{task_id}')
    clips_dir = os.path.join(task_dir, 'clips')
    os.makedirs(clips_dir, exist_ok=True)

    if output_path is None:
        output_path = os.path.join(task_dir, 'final.mp4')
    else:
        if not os.path.isabs(output_path):
            output_path = os.path.join(output_dir, output_path)
        out_parent = os.path.dirname(os.path.abspath(output_path))
        if out_parent != task_dir:
            task_dir = out_parent
            clips_dir = os.path.join(task_dir, 'clips')
            os.makedirs(clips_dir, exist_ok=True)

    fps = 24
    h3_steps = 20

    meta = {
        'task_id': task_id,
        'ref_images': ref_images,
        'script_path': script_path,
        'ref_image_size': ref_image_size,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'clips': [],
    }

    print(f'[{task_id}] Custom video generation')
    print(f'  refs: {ref_images}')
    print(f'  script: {script_path}')

    # ── 1. 解析脚本 ──
    print('\n=== [1] 解析脚本 ===')
    parsed = parse_script(script_path)
    print(f'  scene_setting: {parsed["scene_setting"][:60]}...')
    print(f'  character_setting: {parsed["character_setting"][:60]}...')
    print(f'  shots: {len(parsed["shots"])}')
    for s in parsed['shots']:
        print(f'    {s["id"]}: {s["title"]} ({s["duration"]}s)')
    meta['parsed_script'] = parsed

    # ── 2. LLM 看参考图 → 角色描述 ──
    print('\n=== [2] 角色描述（多模态）===')
    char_descs = []
    name_map = {'doubao': '豆包', 'naiwa': '奶蛙'}
    for ref_path in ref_images:
        name = os.path.splitext(os.path.basename(ref_path))[0]
        cn_name = name_map.get(name, name)
        print(f'  describing {name} ({cn_name}) from {ref_path}...')
        try:
            desc = llm.describe_character(ref_path, name=name)
            print(f'  {name}: {desc[:100]}...')
        except Exception as e:
            print(f'  ⚠ LLM multimodal failed ({e}), using name-only fallback')
            desc = cn_name
        char_descs.append(desc)
    meta['char_descs'] = char_descs

    video_paths = []

    # ── 3. 逐镜生成 ──
    for i, shot in enumerate(parsed['shots']):
        sid = shot['id']
        duration = shot['duration']
        length = int(duration * fps)

        print(f'\n=== [3.{i+1}] shot {sid}: {shot["title"]} ({duration}s, {length} frames) ===')

        # 3a. 构建 H3 ref2va prompt（中文直传，H3 文本编码器 Qwen3-VL 原生支持中文）
        char_names = []
        for ref_path in ref_images:
            char_names.append(os.path.splitext(os.path.basename(ref_path))[0])

        picture_tags = []
        for idx, name in enumerate(char_names):
            cn = name_map.get(name, name)
            desc = char_descs[idx] if idx < len(char_descs) else cn
            picture_tags.append(f"<Picture {idx+1}> 是{cn}（{name}）：{desc}")
        picture_text = '\n'.join(picture_tags)

        constraints = (
            f"画面中只能出现这{len(char_names)}个角色，"
            "外观必须与参考图完全一致，不要创造或添加任何其他角色。"
        )

        scene_desc = f"场景：{parsed['scene_setting']}。镜头：{shot['desc']}"

        h3_prompt = f"{picture_text}\n\n{constraints}\n\n{scene_desc}"
        print(f'  h3_prompt: {h3_prompt[:300]}...')

        # 3b. H3 ref2va 生成
        video_path = os.path.join(clips_dir, f'{sid}.mp4')
        seed = random.randint(0, 2**32 - 1)
        print(f'  seed={seed}, length={length}, ref_image_size={ref_image_size}')

        h3.generate_h3_ref2v(
            prompt=h3_prompt,
            ref_image_paths=ref_images,
            seed=seed,
            width=1344, height=768,
            length=length,
            steps=h3_steps,
            ref_image_size=ref_image_size,
            filename_prefix=f'custom_{task_id}/{sid}',
            output_path=video_path,
        )
        print(f'  video: {video_path}')

        clip_info = {
            'shot_id': sid,
            'title': shot['title'],
            'duration': duration,
            'length': length,
            'seed': seed,
            'h3_prompt': h3_prompt,
            'ref_image_size': ref_image_size,
            'video': video_path,
        }
        meta['clips'].append(clip_info)
        video_paths.append(video_path)

    # ── 4. RIFE 过渡（可选）──
    transition_clips = None
    if not post_kwargs.get('no_rife') and len(video_paths) >= 2:
        print(f'\n=== [4] RIFE 镜头间过渡 ===')
        transition_clips = post.generate_transitions(video_paths, clips_dir, task_id)
        meta['transitions'] = transition_clips

    # ── 5. 拼接 ──
    print(f'\n=== [5] 拼接（保留音频）===')
    concat_videos(video_paths, output_path, transition_clips)
    print(f'  final: {output_path}')

    dur = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', output_path],
        capture_output=True, text=True).stdout.strip()
    total_duration = float(dur) if dur else 0
    print(f'  duration: {total_duration:.1f}s')

    # ── 6. 后处理：LUT 调色 / BGM / STT ──
    concept_text = parsed.get('scene_setting', '')

    # LUT 调色
    if not post_kwargs.get('no_color'):
        lut_override = post_kwargs.get('lut_override')
        if lut_override or post.color_config.get('enabled', False):
            print(f'\n=== [6] LUT 调色 ===')
            lut_path = post.select_lut(concept_text, parsed, task_dir, lut_override)
            if lut_path:
                print(f'  LUT: {os.path.basename(lut_path)}')
                if post.apply_lut(output_path, lut_path):
                    meta['lut'] = lut_path

    # BGM 配乐
    if not post_kwargs.get('no_bgm'):
        bgm_override = post_kwargs.get('bgm_override')
        if bgm_override or post.bgm_config.get('enabled', False):
            print(f'\n=== [7] BGM 配乐 ===')
            bgm_path = post.select_bgm(concept_text, parsed, task_dir,
                                       total_duration + 2.0, bgm_override)
            if bgm_path:
                print(f'  BGM: {os.path.basename(bgm_path)}')
                if post.add_bgm(output_path, bgm_path):
                    meta['bgm'] = bgm_path

    # STT 字幕
    if post_kwargs.get('use_stt'):
        print(f'\n=== [8] STT 字幕 ===')
        stt_srt = post.run_stt(output_path, task_dir)
        if stt_srt:
            meta['srt'] = stt_srt
            print(f'  STT SRT: {stt_srt}')

    meta['output'] = output_path
    meta['status'] = 'completed'

    meta_path = os.path.join(task_dir, 'meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f'\n=== 完成 ===')
    print(f'  成片: {output_path}')
    print(f'  元数据: {meta_path}')
    print(f'  任务目录: {task_dir}')

    return meta


if __name__ == '__main__':
    import argparse
    print('提示: 推荐使用统一入口 → python core/vidance.py custom ...\n')
    parser = argparse.ArgumentParser(description='Custom video generation (内部入口，推荐使用 core/vidance.py)')
    parser.add_argument('--ref', action='append', required=True,
                        help='Character reference image (repeat for multiple characters)')
    parser.add_argument('--script', required=True,
                        help='Path to script file (prompt.txt format)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output video path')
    parser.add_argument('--ref-image-size', choices=['match', 'max'], default='match',
                        help='H3 ref image sizing: match (faster) or max (2048px, best identity)')
    args = parser.parse_args()

    run_custom(
        ref_images=args.ref,
        script_path=args.script,
        output_path=args.output,
        ref_image_size=args.ref_image_size,
    )
