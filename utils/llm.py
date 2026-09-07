#!/usr/bin/env python3
"""LLM 客户端 — 调用 USTC LLM API（文本 + 多模态）

用法:
  from utils.llm import LLMClient
  client = LLMClient()
  script = client.script_write("一只猫在月球上跳舞")
  review = client.review_shot(scene_desc, [frame1_path, frame2_path])
"""
import json
import os
import base64
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class LLMClient:
    def __init__(self, config=None):
        self.config = config or load_config()
        llm_cfg = self.config['llm']
        self.base_url = llm_cfg['base_url']
        self.api_key = llm_cfg['api_key']
        self.models = llm_cfg['models']

    def chat(self, messages: list, model: str = None, temperature: float = 0.7,
             max_tokens: int = 4096, timeout: int = 300) -> str:
        """调用 LLM 文本对话，返回 assistant 回复文本"""
        model = model or self.models['script']
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}/chat/completions',
            data=data,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
            return result['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            raise RuntimeError(f'LLM API {e.code}: {e.read().decode()}')

    def chat_json(self, messages: list, model: str = None,
                  temperature: float = 0.7, timeout: int = 300) -> dict:
        """调用 LLM 并解析 JSON 结果（提取 ```json 代码块或直接解析）"""
        text = self.chat(messages, model=model, temperature=temperature, timeout=timeout)
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从 LLM 回复中提取 JSON（支持 ```json 代码块和裸 JSON）"""
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            text = '\n'.join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1:
                return json.loads(text[start:end + 1])
            raise

    @staticmethod
    def _image_to_base64(path: str) -> str:
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        ext = os.path.splitext(path)[1].lower()
        mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'webp': 'webp'}
        ext = ext.lstrip('.')
        return f'data:image/{mime.get(ext, "jpeg")};base64,{data}'

    def script_write(self, concept: str) -> dict:
        """概念→分镜脚本 JSON"""
        system = (
            '你是视频编剧。根据用户给出的中文概念，创作一个2-5个镜头的短片分镜脚本。\n'
            '输出严格JSON格式，不要加任何解释文字。\n'
            'JSON schema:\n'
            '{\n'
            '  "title": "标题",\n'
            '  "concept": "原始概念",\n'
            '  "style": "英文风格描述，如 cinematic, warm sunset tone, 35mm film grain",\n'
            '  "shots": [\n'
            '    {\n'
            '      "id": 1,\n'
            '      "scene_desc": "中文场景描述，详细描述画面内容",\n'
            '      "narration": "中文旁白文本，字数与时长匹配（中文约4字/秒）",\n'
            '      "duration": 5,\n'
            '      "camera": "镜头描述，如 wide shot, static camera"\n'
            '    }\n'
            '  ]\n'
            '}\n'
            '要求：\n'
            '- 每镜3-5秒（duration字段）\n'
            '- narration字数 = duration × 4（±20%），如5秒约20字\n'
            '- scene_desc要具体，包含主体、动作、环境、光线\n'
            '- 镜头之间有叙事逻辑'
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'概念：{concept}'},
        ]
        return self.chat_json(messages, model=self.models['script'], temperature=0.7)

    def optimize_prompt(self, scene_desc: str, style: str) -> str:
        """中文场景描述→英文 video_prompt"""
        system = (
            '你是视频生成prompt优化器。将中文场景描述翻译为英文的video generation prompt。\n'
            '要求：\n'
            '- 保留所有视觉细节（主体、动作、环境、光线）\n'
            '- 加入风格修饰词（来自style字段）\n'
            '- 英文，不超过80词\n'
            '- 不要加任何解释，只输出prompt文本'
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'场景描述：{scene_desc}\n风格：{style}'},
        ]
        return self.chat(messages, model=self.models['prompt_opt'], temperature=0.5).strip()

    def review_shot(self, scene_desc: str, frame_paths: list,
                    model: str = None) -> dict:
        """多模态审片：关键帧+场景描述→{score, dimensions, feedback, pass}"""
        model = model or self.models['review']
        system = (
            '你是视频审片专家。根据给定的场景描述和关键帧截图，按4个维度打分（每维1-10）：\n'
            '1. consistency: 画面内容与场景描述的匹配度\n'
            '2. quality: 画质（清晰度、色彩、构图）\n'
            '3. motion: 运动自然度、流畅度\n'
            '4. artifact: 无明显AI生成痕迹（畸形、融合、闪烁等）\n'
            '综合分=四维均值。≥7分通过。\n'
            '输出严格JSON格式：\n'
            '{"score": 8, "dimensions": {"consistency": 8, "quality": 9, "motion": 7, "artifact": 8}, '
            '"feedback": "具体反馈", "pass": true}'
        )
        content = [{'type': 'text', 'text': f'场景描述：{scene_desc}\n请按4维度打分'}]
        for path in frame_paths:
            b64 = self._image_to_base64(path)
            content.append({'type': 'image_url', 'image_url': {'url': b64}})

        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': content},
        ]
        return self.chat_json(messages, model=model, temperature=0.3, timeout=600)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='LLM client test')
    parser.add_argument('--concept', default='一只猫在月球上跳舞', help='Concept for script')
    args = parser.parse_args()

    client = LLMClient()
    print(f'=== 编剧: {args.concept} ===')
    script = client.script_write(args.concept)
    print(json.dumps(script, ensure_ascii=False, indent=2))
    print(f'\n=== Prompt优化: {script["shots"][0]["scene_desc"]} ===')
    vp = client.optimize_prompt(script['shots'][0]['scene_desc'], script['style'])
    print(vp)
