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
import mimetypes
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
    def __init__(self, host=None, port=None, config=None, instance='wan'):
        if config is None:
            config = load_config()
        if host and port:
            self.host = host
            self.port = port
        else:
            inst_cfg = config.get('comfyui', {}).get(instance, {})
            self.host = host or inst_cfg.get('host', '127.0.0.1')
            self.port = port or inst_cfg.get('port', 8189)
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

    def upload_image(self, image_path: str, overwrite: bool = False) -> str:
        """Upload an image to ComfyUI's input directory, return the stored filename."""
        filename = os.path.basename(image_path)
        with open(image_path, 'rb') as f:
            file_data = f.read()
        boundary = '----VidanceBoundary' + ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        mime_type = mimetypes.guess_type(filename)[0] or 'image/png'
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n'.encode(),
            file_data,
            b'\r\n',
        ]
        if overwrite:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode())
        parts.append(f'--{boundary}--\r\n'.encode())
        req = urllib.request.Request(
            f'{self.base_url}/upload/image',
            data=b''.join(parts),
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        return result.get('name', filename)

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

    def generate_i2v(self, prompt: str, image_path: str, seed: int = None,
                     width: int = 1280, height: int = 704,
                     length: int = 121, steps: int = 20, cfg: float = 5.0,
                     fps: int = 24, negative_prompt: str = None,
                     filename_prefix: str = 'vidance/shot',
                     output_path: str = None) -> dict:
        """Submit Wan I2V workflow with start_image, return {video_path, seed, params}."""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)
        if negative_prompt is None:
            negative_prompt = NEGATIVE_PROMPT

        image_filename = self.upload_image(image_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'wan_i2v.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'prompt': prompt,
            'negative_prompt': negative_prompt,
            'image_filename': image_filename,
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

        video_info = outputs.get('12', {}).get('images', [{}])[0]
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

    def generate_tripsplat(self, image_path: str, seed: int = None,
                           num_gaussians: int = 262144,
                           filename_prefix: str = 'vidance/character',
                           output_path: str = None) -> dict:
        """Image -> 3DGS (.ply) via TripoSplat. Returns {ply_path, seed, params}."""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        image_filename = self.upload_image(image_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'triposplat.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'image_filename': image_filename,
            'seed': seed,
            'num_gaussians': num_gaussians,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(workflow)
        outputs = self.wait_result(prompt_id, timeout=600)

        # SaveGLB node (id=13) outputs under '3d' key
        file_info = outputs.get('13', {}).get('3d', [{}])[0]
        filename = file_info.get('filename', '')
        subfolder = file_info.get('subfolder', '')
        file_type = file_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No 3D file in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'filename': filename,
            'subfolder': subfolder,
        }

        if output_path:
            self.download_file(filename, subfolder, file_type, output_path)
            result['ply_path'] = output_path

        return result

    def generate_character_anchor(self, image_path: str, seed: int = None,
                                  num_gaussians: int = 262144,
                                  frames: int = 8, width: int = 1024, height: int = 1024,
                                  filename_prefix: str = 'vidance/character',
                                  render_prefix: str = 'vidance/character_render',
                                  output_dir: str = None) -> dict:
        """Image -> 3DGS (.ply) + multi-angle renders in one workflow.

        Returns {ply_path, render_paths, seed, params}.
        """
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        image_filename = self.upload_image(image_path, overwrite=True)

        template_path = os.path.join(WORKFLOW_DIR, 'triposplat_render.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'image_filename': image_filename,
            'seed': seed,
            'num_gaussians': num_gaussians,
            'width': width,
            'height': height,
            'frames': frames,
            'filename_prefix': filename_prefix,
            'render_prefix': render_prefix,
        }
        workflow = render_template(template, params)

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(workflow)
        outputs = self.wait_result(prompt_id, timeout=600)

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'render_paths': [],
        }

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

            # SaveGLB node (id=13) -> .ply
            ply_info = outputs.get('13', {}).get('3d', [{}])[0]
            ply_filename = ply_info.get('filename', '')
            if ply_filename:
                ply_path = os.path.join(output_dir, ply_filename)
                self.download_file(ply_filename, ply_info.get('subfolder', ''),
                                   ply_info.get('type', 'output'), ply_path)
                result['ply_path'] = ply_path

            # SaveImage node (id=15) -> rendered images
            for img_info in outputs.get('15', {}).get('images', []):
                img_filename = img_info.get('filename', '')
                if img_filename:
                    img_path = os.path.join(output_dir, img_filename)
                    self.download_file(img_filename, img_info.get('subfolder', ''),
                                       img_info.get('type', 'output'), img_path)
                    result['render_paths'].append(img_path)

        return result

    def generate_flux_t2i(self, prompt: str, seed: int = None,
                          width: int = 1024, height: int = 1024,
                          steps: int = 20, guidance: float = 3.5, cfg: float = 1.0,
                          filename_prefix: str = 'vidance/flux',
                          output_path: str = None) -> dict:
        """FLUX T2I: text prompt → image. Returns {image_path, seed, params}."""
        if seed is None:
            seed = random.randint(0, 2**31 - 1)

        template_path = os.path.join(WORKFLOW_DIR, 'flux_t2i.json')
        with open(template_path, 'r') as f:
            template = f.read()

        params = {
            'prompt': prompt,
            'width': width,
            'height': height,
            'steps': steps,
            'guidance': guidance,
            'cfg': cfg,
            'seed': seed,
            'filename_prefix': filename_prefix,
        }
        workflow = render_template(template, params)

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(workflow)
        outputs = self.wait_result(prompt_id, timeout=600)

        # SaveImage node (id=9) outputs under 'images' key
        img_info = outputs.get('9', {}).get('images', [{}])[0]
        filename = img_info.get('filename', '')
        subfolder = img_info.get('subfolder', '')
        file_type = img_info.get('type', 'output')

        if not filename:
            raise RuntimeError(f'No image in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': params,
            'filename': filename,
            'subfolder': subfolder,
        }

        if output_path:
            self.download_file(filename, subfolder, file_type, output_path)
            result['image_path'] = output_path

        return result


    def generate_h3_ref2v(self, prompt: str, ref_image_paths: list,
                          seed: int = None,
                          width: int = 1344, height: int = 768,
                          length: int = 124, steps: int = 20,
                          ref_image_size: str = "match",
                          filename_prefix: str = 'vidance/h3_ref2v',
                          output_path: str = None) -> dict:
        """H3 ref2va: reference images + prompt -> video with native audio.

        Reference images are re-injected at every denoising step to lock
        character identity.  Prompt uses <Picture 1>, <Picture 2>, ... to
        reference images in order.
        """
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        # Snap frame count to H3's 17k+5 grid
        aligned_length = length
        while aligned_length % 17 != 5:
            aligned_length += 1

        # Upload reference images
        ref_image_names = []
        for ref_path in ref_image_paths:
            name = self.upload_image(ref_path, overwrite=True)
            ref_image_names.append(name)

        # Build workflow programmatically (ref image count is variable)
        wf = {}
        wf["1"] = {"class_type": "UNETLoader", "inputs": {
            "unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            "weight_dtype": "default",
        }}
        wf["2"] = {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "type": "minimax",
        }}
        wf["3"] = {"class_type": "VAELoader", "inputs": {
            "vae_name": "minimax_h3_video_vae_fp16.safetensors",
        }}
        wf["4"] = {"class_type": "VAELoader", "inputs": {
            "vae_name": "minimax_h3_audio_vae_fp32.safetensors",
        }}

        next_id = 5
        load_image_ids = []
        for img_name in ref_image_names:
            nid = str(next_id)
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
            load_image_ids.append(nid)
            next_id += 1

        r2v_id = str(next_id)
        r2v_inputs = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "audio_vae": ["4", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": aligned_length,
            "ref_image_size": ref_image_size,
        }
        for i, img_nid in enumerate(load_image_ids):
            r2v_inputs[f"ref_images.ref_image_{i}"] = [img_nid, 0]
        wf[r2v_id] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": r2v_inputs}
        next_id += 1

        ks_id = str(next_id)
        wf[ks_id] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}}
        next_id += 1

        bs_id = str(next_id)
        wf[bs_id] = {"class_type": "BasicScheduler", "inputs": {
            "model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0,
        }}
        next_id += 1

        rn_id = str(next_id)
        wf[rn_id] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        next_id += 1

        bg_id = str(next_id)
        wf[bg_id] = {"class_type": "BasicGuider", "inputs": {
            "model": ["1", 0], "conditioning": [r2v_id, 0],
        }}
        next_id += 1

        sca_id = str(next_id)
        wf[sca_id] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": [rn_id, 0], "guider": [bg_id, 0],
            "sampler": [ks_id, 0], "sigmas": [bs_id, 0],
            "latent_image": [r2v_id, 1],
        }}
        next_id += 1

        vd_id = str(next_id)
        wf[vd_id] = {"class_type": "VAEDecode", "inputs": {
            "samples": [sca_id, 0], "vae": ["3", 0],
        }}
        next_id += 1

        vda_id = str(next_id)
        wf[vda_id] = {"class_type": "VAEDecodeAudio", "inputs": {
            "samples": [sca_id, 0], "vae": ["4", 0],
        }}
        next_id += 1

        cv_id = str(next_id)
        wf[cv_id] = {"class_type": "CreateVideo", "inputs": {
            "images": [vd_id, 0], "fps": 24, "audio": [vda_id, 0],
        }}
        next_id += 1

        sv_id = str(next_id)
        wf[sv_id] = {"class_type": "SaveVideo", "inputs": {
            "video": [cv_id, 0],
            "filename_prefix": filename_prefix,
            "format": "mp4",
            "codec": "auto",
        }}

        if not self.wait_ready(timeout=60):
            raise RuntimeError('ComfyUI server not ready')

        prompt_id = self.queue_prompt(wf)
        outputs = self.wait_result(prompt_id, timeout=7200)

        # SaveVideo outputs under "images" key (PreviewVideo reuses image UI channel)
        video_info = outputs.get(sv_id, {}).get("images", [{}])[0]
        filename = video_info.get("filename", "")
        subfolder = video_info.get("subfolder", "")
        file_type = video_info.get("type", "output")

        if not filename:
            raise RuntimeError(f'No video in outputs: {outputs}')

        result = {
            'prompt_id': prompt_id,
            'seed': seed,
            'params': {
                'prompt': prompt,
                'ref_image_paths': ref_image_paths,
                'width': width,
                'height': height,
                'length': aligned_length,
                'steps': steps,
                'ref_image_size': ref_image_size,
            },
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
