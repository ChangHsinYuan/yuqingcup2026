#!/usr/bin/env python3
"""ComfyUI HTTP 客户端 — 提交 workflow、轮询结果、下载视频

用法:
  from utils.comfy_api import ComfyClient
  client = ComfyClient(host='127.0.0.1', port=8189)
  result = client.generate_t2v(
      prompt="a cat on the moon",
      seed=random.randint(0, 2**32),
      output_path='output/clips/shot_1.mp4',
  )
"""
import json
import os
import sys
import time
import random
import string
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
WORKFLOW_DIR = os.path.join(os.path.dirname(__file__), 'workflows')

NEGATIVE_PROMPT = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，"
    "静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
    "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
    "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，"
    "背景人很多，倒着走"
)


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def render_template(template_str: str, params: dict) -> dict:
    """将 {{var}} 占位符替换为实际值，返回解析后的 JSON dict"""
    result = template_str
    for key, value in params.items():
        placeholder = '{{' + key + '}}'
        if isinstance(value, str):
            replacement = json.dumps(value, ensure_ascii=False)
        else:
            replacement = str(value)
        result = result.replace(placeholder, replacement)
    return json.loads(result)


class ComfyClient:
    def __init__(self, host='127.0.0.1', port=8189, config=None):
        if config is None:
            config = load_config()
        wan_cfg = config.get('comfyui', {}).get('wan', {})
        self.host = host or wan_cfg.get('host', '127.0.0.1')
        self.port = port or wan_cfg.get('port', 8189)
        self.base_url = f'http://{self.host}:{self.port}'

    def wait_ready(self, timeout=120) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                urllib.request.urlopen(f'{self.base_url}/history', timeout=5)
                return True
            except Exception:
                time.sleep(3)
        return False

    def queue_prompt(self, workflow: dict) -> str:
        data = json.dumps({'prompt': workflow}).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/prompt',
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())['prompt_id']
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'ComfyUI HTTP {e.code}: {e.read().decode()}')

    def wait_result(self, prompt_id: str, timeout=3600) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with urllib.request.urlopen(
                    f'{self.base_url}/history/{prompt_id}', timeout=10
                ) as r:
                    history = json.loads(r.read())
            except Exception:
                time.sleep(3)
                continue
            if prompt_id not in history:
                time.sleep(3)
                continue
            entry = history[prompt_id]
            status = entry.get('status', {})
            if status.get('status_str') == 'error':
                raise RuntimeError(f'ComfyUI execution error: {status.get("messages", "")}')
            outputs = entry.get('outputs')
            if outputs:
                return outputs
            time.sleep(3)
        raise TimeoutError(f'ComfyUI timeout waiting for prompt {prompt_id}')

    def download_file(self, filename: str, subfolder: str, file_type: str,
                      output_path: str) -> str:
        params = f'filename={filename}&subfolder={subfolder}&type={file_type}'
        url = f'{self.base_url}/view?{params}'
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as resp:
            with open(output_path, 'wb') as f:
                f.write(resp.read())
        return output_path

    def generate_t2v(self, prompt: str, seed: int = None,
                     width: int = 1280, height: int = 704,
                     length: int = 121, steps: int = 20, cfg: float = 5.0,
                     fps: int = 24, negative_prompt: str = None,
                     filename_prefix: str = 'vidance/shot',
                     output_path: str = None) -> dict:
        """提交 Wan T2V workflow并等待结果，返回 {video_path, seed, params}"""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)
        if negative_prompt is None:
            negative_prompt = NEGATIVE_PROMPT

        template_path = os.path.join(WORKFLOW_DIR, 'wan_t2v.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'width': width,
            'height': height,
            'length': length,
            'steps': steps,
            'cfg': cfg,
            'seed': seed,
            'fps': fps,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(workflow)
        outputs = self.wait_result(prompt_id)

        # SaveVideo node (id=11) outputs under 'images' key
        video_info = outputs.get('11', {}).get('images', [{}])[0]
        filename = video_info.get('filename', '')
        subfolder = video_info.get('subfolder', '')
        file_type = video_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No video in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'filename': filename,
            'subfolder': subfolder,
        }

        if output_path:
            self.download_file(filename, subfolder, file_type, output_path)
            result['video_path'] = output_path

        return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ComfyUI Wan T2V client')
    parser.add_argument('prompt', help='Video prompt (English recommended)')
    parser.add_argument('-o', '--output', default='/tmp/comfy_t2v_output.mp4')
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=704)
    parser.add_argument('--length', type=int, default=121)
    parser.add_argument('--steps', type=int, default=20)
    parser.add_argument('--cfg', type=float, default=5.0)
    args = parser.parse_args()

    client = ComfyClient()
    print(f'Submitting T2V: prompt="{args.prompt[:60]}..." seed={args.seed or "random"}')
    result = client.generate_t2v(
        prompt=args.prompt,
        seed=args.seed,
        output_path=args.output,
        width=args.width,
        height=args.height,
        length=args.length,
        steps=args.steps,
        cfg=args.cfg,
    )
    print(f'Done: {result.get("video_path", result.get("filename"))}')
    print(f'Seed: {result["seed"]}')
