#!/usr/bin/env python3
"""TTS 客户端 — 调用 CosyVoice2 FastAPI 服务（9880）

用法:
  from utils.tts import TTSClient
  client = TTSClient()
  result = client.synthesize("你好世界", output_path="output/clips/shot_1.wav")
  # result = {audio_path, duration, timestamps}
"""
import json
import os
import sys
import base64
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class TTSClient:
    def __init__(self, config=None):
        self.config = config or load_config()
        tts_cfg = self.config['tts']
        self.base_url = f"http://{tts_cfg['host']}:{tts_cfg['port']}"
        self.default_voice = tts_cfg.get('default_voice', 'default')

    def health(self) -> dict:
        url = f'{self.base_url}/health'
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())

    def synthesize(self, text: str, output_path: str = None,
                   voice: str = None, speed: float = 1.0,
                   prompt_wav: str = None, prompt_text: str = None) -> dict:
        """合成语音，保存到 output_path，返回 {audio_path, duration, timestamps}"""
        voice = voice or self.default_voice
        payload = {'text': text, 'voice': voice, 'speed': speed}
        if prompt_wav:
            payload['prompt_wav'] = prompt_wav
        if prompt_text is not None:
            payload['prompt_text'] = prompt_text

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/tts',
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            audio_bytes = base64.b64decode(result['audio'])
            with open(output_path, 'wb') as f:
                f.write(audio_bytes)
            result['audio_path'] = output_path
            del result['audio']

        return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='CosyVoice TTS client')
    parser.add_argument('text', help='Text to synthesize')
    parser.add_argument('-o', '--output', default='/tmp/tts_cli_output.wav', help='Output wav path')
    parser.add_argument('-v', '--voice', default=None, help='Voice name')
    parser.add_argument('-s', '--speed', type=float, default=1.0, help='Speech speed')
    args = parser.parse_args()

    client = TTSClient()
    print(f'health: {client.health()}')
    result = client.synthesize(args.text, output_path=args.output, voice=args.voice, speed=args.speed)
    print(f'duration: {result["duration"]}s')
    print(f'saved: {result.get("audio_path")}')
    print(f'timestamps: {json.dumps(result["timestamps"], ensure_ascii=False, indent=2)}')
