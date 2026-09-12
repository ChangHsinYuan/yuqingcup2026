#!/usr/bin/env python3
"""Hunyuan3Dv2 重建客户端 — 单图/多视角 → GLB mesh

用法:
  from utils.hunyuan3d import Hunyuan3DClient
  client = Hunyuan3DClient()

  # 单图重建
  glb_path = client.generate_single('character.png', output_path='character.glb')

  # 多视角重建（3 视角：front + left + back）
  glb_path = client.generate_multiview(
      front_path='front.png', left_path='left.png', back_path='back.png',
      output_path='character.glb',
  )
"""
import os
import random

from .comfy_api import ComfyClient, render_template, WORKFLOW_DIR


class Hunyuan3DClient:
    """Hunyuan3Dv2 turbo 重建客户端（ComfyUI 8193, GPU1）。

    4 步一致性蒸馏，~61s(冷)/~22s(热)。
    """

    def __init__(self, config=None):
        self.client = ComfyClient(config=config, instance='hunyuan3d')

    def generate_single(self, image_path: str, seed: int = None,
                        filename_prefix: str = 'vidance/hunyuan3d',
                        output_path: str = None) -> dict:
        """单图 → GLB mesh。返回 {glb_path, seed, params}。"""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        image_filename = self.client.upload_image(image_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'hunyuan3d_single.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'image_filename': image_filename,
            'seed': seed,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.client.wait_ready(timeout=60):
            raise RuntimeError('Hunyuan3Dv2 ComfyUI server not ready')

        prompt_id = self.client.queue_prompt(workflow)
        outputs = self.client.wait_result(prompt_id, timeout=300)

        # SaveGLB node id=11 in hunyuan3d_single.json
        file_info = outputs.get('11', {}).get('3d', [{}])[0]
        filename = file_info.get('filename', '')
        subfolder = file_info.get('subfolder', '')
        file_type = file_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No GLB in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'filename': filename,
            'subfolder': subfolder,
        }

        if output_path:
            self.client.download_file(filename, subfolder, file_type, output_path)
            result['glb_path'] = output_path

        return result

    def generate_multiview(self, front_path: str, left_path: str, back_path: str,
                           seed: int = None,
                           filename_prefix: str = 'vidance/hunyuan3d',
                           output_path: str = None) -> dict:
        """多视角（front + left + back）→ GLB mesh。返回 {glb_path, seed, params}。"""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        front_name = self.client.upload_image(front_path, overwrite=True)
        left_name = self.client.upload_image(left_path, overwrite=True)
        back_name = self.client.upload_image(back_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'hunyuan3d_multiview.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'front_image': front_name,
            'left_image': left_name,
            'back_image': back_name,
            'seed': seed,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.client.wait_ready(timeout=60):
            raise RuntimeError('Hunyuan3Dv2 ComfyUI server not ready')

        prompt_id = self.client.queue_prompt(workflow)
        outputs = self.client.wait_result(prompt_id, timeout=300)

        # SaveGLB node id=15 in hunyuan3d_multiview.json
        file_info = outputs.get('15', {}).get('3d', [{}])[0]
        filename = file_info.get('filename', '')
        subfolder = file_info.get('subfolder', '')
        file_type = file_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No GLB in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'filename': filename,
            'subfolder': subfolder,
        }

        if output_path:
            self.client.download_file(filename, subfolder, file_type, output_path)
            result['glb_path'] = output_path

        return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Hunyuan3Dv2 reconstruction client')
    parser.add_argument('image', help='Input image path (single-image mode)')
    parser.add_argument('-o', '--output', default='/tmp/hunyuan3d_output.glb')
    parser.add_argument('--seed', type=int, default=None)
    args = parser.parse_args()

    client = Hunyuan3DClient()
    print(f'Reconstructing: {args.image}')
    result = client.generate_single(
        image_path=args.image,
        seed=args.seed,
        output_path=args.output,
    )
    print(f'Done: {result.get("glb_path", result.get("filename"))}')
    print(f'Seed: {result["seed"]}')
