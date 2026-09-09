#!/usr/bin/env python3
"""RIFE 光流插帧客户端 — 镜头间过渡 + 单镜慢动作

用法:
  from utils.rife import RIFEClient
  client = RIFEClient(host='127.0.0.1', port=8189)
  # 镜头间过渡
  client.interpolate_transition(frame_a, frame_b, output_path, multiplier=8)
  # 单镜慢动作
  client.slowmo(video_path, output_path, multiplier=2)
"""
import json
import os
import time
import random
import string
import subprocess
import urllib.request

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.comfy_api import ComfyClient, render_template, WORKFLOW_DIR


class RIFEClient(ComfyClient):
    """RIFE 插帧客户端，复用 ComfyClient 的 HTTP 通信。"""

    def __init__(self, host=None, port=None, config=None, instance='wan',
                 model_name='rife_v4.26.safetensors'):
        super().__init__(host, port, config, instance)
        self.model_name = model_name

    def interpolate_transition(self, frame_a_path, frame_b_path, output_path,
                               multiplier=8, fps=24,
                               filename_prefix='vidance/transition') -> dict:
        """两帧之间光流插帧，生成过渡视频片段。

        Args:
            frame_a_path: 起始帧图片路径
            frame_b_path: 结束帧图片路径
            output_path: 输出视频路径
            multiplier: 插帧倍率（2帧之间生成 multiplier-1 个中间帧）
            fps: 输出帧率

        Returns:
            {video_path, frame_count, multiplier, fps}
        """
        frame_a_name = self.upload_image(frame_a_path, overwrite=True)
        frame_b_name = self.upload_image(frame_b_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'rife_transition.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'frame_a_name': frame_a_name,
            'frame_b_name': frame_b_name,
            'model_name': self.model_name,
            'multiplier': multiplier,
            'fps': fps,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(workflow)
        outputs = self.wait_result(prompt_id)

        video_info = outputs.get('7', {}).get('videos', [{}])[0]
        if not video_info:
            video_info = outputs.get('7', {}).get('images', [{}])[0]
        filename = video_info.get('filename', '')
        subfolder = video_info.get('subfolder', '')
        file_type = video_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No video in RIFE outputs: {outputs}')

        if output_path:
            self.download_file(filename, subfolder, file_type, output_path)

        return {
            'video_path': output_path,
            'frame_count': multiplier + 1,
            'multiplier': multiplier,
            'fps': fps,
        }

    def slowmo(self, video_path, output_path, multiplier=2, fps=24) -> dict:
        """单镜帧倍增慢动作：用 ffmpeg 提取所有帧 → RIFE 插帧 → 重组视频。

        Args:
            video_path: 原始视频路径
            output_path: 慢动作输出路径
            multiplier: 帧倍率（multiplier=2 → 2倍帧数 → 2倍时长慢放）

        Returns:
            {video_path, multiplier, fps}
        """
        import tempfile
        from PIL import Image

        tmpdir = tempfile.mkdtemp(prefix='rife_slowmo_')

        try:
            # 1. 用 ffmpeg 提取所有帧
            frame_pattern = os.path.join(tmpdir, 'frame_%06d.png')
            subprocess.run(
                ['ffmpeg', '-y', '-i', video_path, '-q:v', '2', frame_pattern],
                capture_output=True, timeout=120,
            )

            frames = sorted([f for f in os.listdir(tmpdir) if f.startswith('frame_') and f.endswith('.png')])
            if len(frames) < 2:
                raise RuntimeError(f'Not enough frames extracted: {len(frames)}')

            # 2. 批量插帧（每对相邻帧之间插 multiplier-1 个中间帧）
            interpolated_frames = [os.path.join(tmpdir, frames[0])]
            for i in range(len(frames) - 1):
                frame_a = os.path.join(tmpdir, frames[i])
                frame_b = os.path.join(tmpdir, frames[i + 1])

                # 上传并插帧
                interp_output = os.path.join(tmpdir, f'interp_{i:06d}')
                os.makedirs(interp_output, exist_ok=True)

                frame_a_name = self.upload_image(frame_a, overwrite=True)
                frame_b_name = self.upload_image(frame_b, overwrite=True)

                template_path = os.path.join(WORKFLOW_DIR, 'rife_transition.json')
                with open(template_path, 'r') as f:
                    template = f.read()

                params = {
                    'frame_a_name': frame_a_name,
                    'frame_b_name': frame_b_name,
                    'model_name': self.model_name,
                    'multiplier': multiplier,
                    'fps': fps,
                    'filename_prefix': f'vidance/slowmo_{i:06d}',
                }
                workflow = render_template(template, params)

                if not self.wait_ready(timeout=60):
                    raise RuntimeError('ComfyUI server not ready')

                prompt_id = self.queue_prompt(workflow)
                outputs = self.wait_result(prompt_id)

                # 下载插帧结果视频
                video_info = outputs.get('7', {}).get('videos', [{}])[0]
                if not video_info:
                    video_info = outputs.get('7', {}).get('images', [{}])[0]
                filename = video_info.get('filename', '')
                subfolder = video_info.get('subfolder', '')
                file_type = video_info.get('type', 'output')

                interp_video = os.path.join(interp_output, 'interp.mp4')
                self.download_file(filename, subfolder, file_type, interp_video)

                # 从插帧视频提取中间帧（跳过首尾帧，首帧已在列表中）
                mid_pattern = os.path.join(interp_output, 'mid_%06d.png')
                subprocess.run(
                    ['ffmpeg', '-y', '-i', interp_video, '-q:v', '2', mid_pattern],
                    capture_output=True, timeout=60,
                )
                mid_frames = sorted([f for f in os.listdir(interp_output) if f.startswith('mid_')])
                for mf in mid_frames[1:-1]:  # skip first (dup) and last (dup)
                    interpolated_frames.append(os.path.join(interp_output, mf))

                interpolated_frames.append(os.path.join(tmpdir, frames[i + 1]))

            # 3. 用 ffmpeg 重组慢动作视频
            list_file = os.path.join(tmpdir, 'filelist.txt')
            with open(list_file, 'w') as f:
                for fp in interpolated_frames:
                    f.write(f"file '{fp}'\n")

            subprocess.run(
                ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                 '-r', str(fps), '-c:v', 'libx264', '-crf', '18',
                 '-pix_fmt', 'yuv420p', output_path],
                capture_output=True, timeout=300,
            )

            return {
                'video_path': output_path,
                'multiplier': multiplier,
                'fps': fps,
                'frame_count': len(interpolated_frames),
            }

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


def extract_frame(video_path, frame_idx, output_path):
    """从视频提取指定帧为图片"""
    subprocess.run(
        ['ffmpeg', '-y', '-i', video_path, '-vf',
         f'select=eq(n\\,{frame_idx})', '-vframes', '1',
         '-q:v', '2', output_path],
        capture_output=True, timeout=30,
    )
    return output_path


def get_video_frame_count(video_path):
    """获取视频帧数"""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=nb_frames', '-of', 'csv=p=0', video_path],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='RIFE frame interpolation')
    parser.add_argument('frame_a', help='First frame image path')
    parser.add_argument('frame_b', help='Second frame image path')
    parser.add_argument('-o', '--output', default='transition.mp4', help='Output video path')
    parser.add_argument('-m', '--multiplier', type=int, default=8, help='Interpolation multiplier')
    parser.add_argument('--fps', type=float, default=24, help='Output FPS')
    parser.add_argument('--model', default='rife_v4.26.safetensors', help='RIFE model name')
    args = parser.parse_args()

    client = RIFEClient(model_name=args.model)
    result = client.interpolate_transition(
        args.frame_a, args.frame_b, args.output,
        multiplier=args.multiplier, fps=args.fps,
    )
    print(f"Done: {result}")
