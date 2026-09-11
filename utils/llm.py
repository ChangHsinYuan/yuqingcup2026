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
import io
import urllib.request
import urllib.error
from PIL import Image

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
             max_tokens: int = 4096, timeout: int = 300, retries: int = 2) -> str:
        """调用 LLM 文本对话，返回 assistant 回复文本（超时自动重试）"""
        model = model or self.models['script']
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        last_err = None
        for attempt in range(retries + 1):
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
            except Exception as e:
                last_err = e
                if attempt < retries:
                    import time
                    time.sleep(3)
        raise last_err

    def chat_json(self, messages: list, model: str = None,
                  temperature: float = 0.7, timeout: int = 300, retries: int = 0) -> dict:
        """调用 LLM 并解析 JSON 结果（提取 ```json 代码块或直接解析）"""
        text = self.chat(messages, model=model, temperature=temperature, timeout=timeout, retries=retries)
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
    def _image_to_base64(path: str, max_size: int = 768) -> str:
        """读取图片并转为 base64 data URL，自动缩放到 max_size 内以减少 payload"""
        img = Image.open(path)
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        w, h = img.size
        scale = min(1.0, max_size / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        data = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{data}'

    def script_write(self, concept: str, character_desc: str = None,
                     target_duration: float = None) -> dict:
        """概念→分镜脚本 JSON（v1: 含角色锚 + 每镜 camera 角度 + 背景描述）

        Args:
            target_duration: 目标总时长（秒）。None 时默认 2-5 镜短片，
                指定时按每镜~4s 动态计算镜头数（解锁长片）。
        """
        char_section = ''
        if character_desc:
            char_section = (
                f'  "character": {{\n'
                f'    "desc": "{character_desc}"\n'
                f'  }},\n'
            )
        if target_duration:
            n_shots = max(2, round(target_duration / 4))
            shot_range = f'{max(2, n_shots - 2)}-{n_shots + 2}'
            shot_instruction = f'创作一个约{shot_range}个镜头的分镜脚本，总时长约{target_duration:.0f}秒。'
        else:
            shot_instruction = '创作一个2-5个镜头的短片分镜脚本。'

        system = (
            f'你是视频编剧。根据用户给出的中文概念，{shot_instruction}\n'
            '输出严格JSON格式，不要加任何解释文字。\n'
            'JSON schema:\n'
            '{\n'
            f'{char_section}'
            '  "title": "标题",\n'
            '  "concept": "原始概念",\n'
            '  "style": "英文风格描述，如 cinematic, warm sunset tone, 35mm film grain",\n'
            '  "shots": [\n'
            '    {\n'
            '      "id": 1,\n'
            '      "scene_desc": "中文场景描述，详细描述画面内容（含角色动作）",\n'
            '      "narration": "中文旁白文本，字数与时长匹配（中文约4字/秒）",\n'
            '      "background_desc": "中文背景描述，只描述环境不含角色",\n'
            '      "duration": 5,\n'
            '      "camera": {\n'
            '        "yaw": 0,\n'
            '        "pitch": 15,\n'
            '        "fov": 35,\n'
            '        "distance": 0,\n'
            '        "desc": "镜头描述，如 wide shot, slow pan"\n'
            '      },\n'
            '      "slowmo": {"multiplier": 2, "mode": "slowmo"},\n'
            '      "transition_out": "rife"\n'
            '    }\n'
            '  ]\n'
            '}\n'
            '要求：\n'
            '- 每镜3-5秒（duration字段）\n'
            '- narration字数 = duration × 4（±20%），如5秒约20字\n'
            '- scene_desc要具体，包含主体、动作、环境、光线\n'
            '- background_desc只描述场景环境，不包含角色\n'
            '- camera.yaw是绕角色旋转的水平角度（0=正面，90=右侧，180=背面，270=左侧）\n'
            '- camera.pitch是俯仰角度（0=平视，正值俯视，负值仰视，范围-30到30）\n'
            '- camera.fov是视野角度（35=窄角特写，50=标准，70=广角）\n'
            '- camera.distance填0即可（自动取景）\n'
            '- 不同镜头用不同yaw角度展示角色多角度\n'
            '- 镜头之间有叙事逻辑\n'
            '- slowmo: 可选，对需要慢动作的镜头设置。multiplier为帧倍率（2=2倍慢放），mode为"slowmo"（慢放）或"smooth"（高帧率平滑）。不需要慢动作的镜头省略此字段\n'
            '- transition_out: 此镜头到下一镜头的过渡方式。"rife"=光流插帧（默认，适合相邻场景），"crossfade"=交叉淡化（适合场景跳跃），"cut"=硬切（适合强对比）。最后一镜可省略'
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

    def optimize_character_prompt(self, character_desc: str) -> str:
        """中文角色描述→英文 FLUX character_prompt（正面站立、纯色背景、角色参考图）"""
        system = (
            '你是角色设计prompt优化器。将中文角色描述翻译为英文的FLUX image generation prompt。\n'
            '要求：\n'
            '- 保留角色所有外观细节（物种、毛色/肤色、服装、配饰、体型）\n'
            '- 角色必须是自然站立姿态（standing upright on the ground, natural pose），绝不能躺倒、侧卧或悬浮在空中\n'
            '- 固定后缀：standing upright on the ground, full body, front view, plain white background, high detail, character reference sheet\n'
            '- 英文，不超过80词\n'
            '- 不要加任何解释，只输出prompt文本'
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'角色描述：{character_desc}'},
        ]
        return self.chat(messages, model=self.models['prompt_opt'], temperature=0.5).strip()

    def optimize_scene_prompt(self, character_desc: str, scene_desc: str,
                              style: str) -> str:
        """中文角色描述+场景描述→英文 FLUX 场景图 prompt（角色自然融入场景，站立姿态）"""
        system = (
            '你是场景图生成prompt优化器。将中文角色描述和场景描述组合为英文的FLUX image generation prompt。\n'
            '要求：\n'
            '- 角色自然融入场景中（角色是画面主体之一，但场景环境完整呈现）\n'
            '- 保留角色所有外观细节（物种、毛色/肤色、服装、配饰）\n'
            '- 角色必须是自然姿态（如站立、行走、跳跃），与场景互动，绝不能躺倒或无故悬浮\n'
            '- 保留场景所有环境细节（地形、天空、光线、建筑）\n'
            '- 加入风格修饰词（来自style字段）\n'
            '- 英文，不超过100词\n'
            '- 不要加任何解释，只输出prompt文本'
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'角色描述：{character_desc}\n场景描述：{scene_desc}\n风格：{style}'},
        ]
        return self.chat(messages, model=self.models['prompt_opt'], temperature=0.5).strip()

    def optimize_background_prompt(self, background_desc: str, style: str) -> str:
        """中文背景描述→英文 FLUX background_prompt（无角色）"""
        system = (
            '你是背景生成prompt优化器。将中文场景环境描述翻译为英文的FLUX image generation prompt。\n'
            '要求：\n'
            '- 只描述环境（地形、天空、建筑、植物、光线、天气），不包含任何角色或人物\n'
            '- 加入风格修饰词（来自style字段）\n'
            '- 固定后缀：no character, no person, empty scene, cinematic\n'
            '- 英文，不超过60词\n'
            '- 不要加任何解释，只输出prompt文本'
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'背景描述：{background_desc}\n风格：{style}'},
        ]
        return self.chat(messages, model=self.models['prompt_opt'], temperature=0.5).strip()

    def select_lut(self, concept: str, script: dict, available_styles: list) -> str:
        """根据脚本内容自动选择最佳 LUT 调色风格。

        Args:
            concept: 用户概念描述
            script: 编剧脚本 dict（含 title, shots）
            available_styles: 可用 LUT 风格列表

        Returns:
            选中的风格名（如 'cinematic'）
        """
        style_descs = {
            'cinematic': '青橙色调，经典电影感，适合冒险/剧情/史诗',
            'warm': '暖金色调，温馨柔和，适合日常/治愈/家庭',
            'cool': '冷蓝色调，清冷神秘，适合夜景/科幻/悬疑',
            'vintage': '复古褪色，怀旧质感，适合回忆/纪录片/复古',
            'vivid': '高饱和高对比，鲜艳夺目，适合旅游/风景/广告',
            'soft': '柔和 pastel，淡雅梦幻，适合童话/浪漫/治愈',
        }
        options = '\n'.join(
            f'- {s}: {style_descs.get(s, s)}' for s in available_styles
        )

        system = (
            '你是视频调色师。根据视频概念和分镜内容，选择最合适的调色风格。\n'
            f'可选风格：\n{options}\n'
            '只输出风格名称（如 cinematic），不要加任何解释。'
        )
        shots_summary = '; '.join(
            f"镜头{s['id']}：{s.get('scene_desc', '')}" for s in script.get('shots', [])
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'概念：{concept}\n标题：{script.get("title", "")}\n分镜：{shots_summary}'},
        ]
        result = self.chat(messages, model=self.models['prompt_opt'], temperature=0.3).strip().lower()
        # 验证返回值
        for style in available_styles:
            if style in result:
                return style
        return available_styles[0]

    def select_bgm_mood(self, concept: str, script: dict, available_moods: list) -> str:
        """根据脚本内容自动选择最佳 BGM 配乐风格。

        Args:
            concept: 用户概念描述
            script: 编剧脚本 dict
            available_moods: 可用 BGM 风格列表

        Returns:
            选中的风格名（如 'calm'）
        """
        mood_descs = {
            'calm': '平静冥想，低频和声，适合治愈/日常/自然',
            'uplifting': '振奋向上，大调琶音，适合冒险/励志/旅行',
            'mysterious': '神秘悬疑，小二度不协和，适合悬疑/探索/夜景',
            'dramatic': '戏剧紧张，低频脉冲，适合战斗/紧张/冲突',
            'playful': '活泼俏皮，五声音阶跳跃，适合童趣/搞笑/轻松',
        }
        options = '\n'.join(
            f'- {m}: {mood_descs.get(m, m)}' for m in available_moods
        )

        system = (
            '你是视频配乐师。根据视频概念和分镜内容，选择最合适的配乐风格。\n'
            f'可选风格：\n{options}\n'
            '只输出风格名称（如 calm），不要加任何解释。'
        )
        shots_summary = '; '.join(
            f"镜头{s['id']}：{s.get('scene_desc', '')}" for s in script.get('shots', [])
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'概念：{concept}\n标题：{script.get("title", "")}\n分镜：{shots_summary}'},
        ]
        result = self.chat(messages, model=self.models['prompt_opt'], temperature=0.3).strip().lower()
        for mood in available_moods:
            if mood in result:
                return mood
        return available_moods[0]

    def describe_character(self, image_path: str, name: str = None) -> str:
        """多模态：看角色三视图→英文详细外观描述（供 FLUX T2I prompt 用）"""
        name_hint = f' This character is called "{name}".' if name else ''
        system = (
            'You are a character designer. Look at the character reference sheet (three-view / turnaround)'
            f' and write a detailed English description of the character\'s appearance.{name_hint}\n'
            'Include: species/type, body shape and proportions, skin/fur/color, facial features, '
            'clothing (style, color, texture, condition), accessories, and any distinctive features.\n'
            'Output ONLY the English description text, no JSON, no explanation, no markdown. '
            'Keep it under 120 words.'
        )
        content = [
            {'type': 'text', 'text': 'Describe this character in detail for an image generation prompt.'},
            {'type': 'image_url', 'image_url': {'url': self._image_to_base64(image_path)}},
        ]
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': content},
        ]
        return self.chat(messages, model=self.models['review'], temperature=0.3, timeout=120, retries=1).strip()

    def combine_scene_prompt(self, char_descs: list, scene_setting: str,
                              shot_desc: str, style: str = '') -> str:
        """角色描述+全局场景+镜头描述→英文 FLUX T2I prompt（多角色场景图）"""
        char_section = '\n'.join(f'Character {i+1}: {d}' for i, d in enumerate(char_descs))
        system = (
            'You are an image generation prompt writer. Combine character descriptions, scene setting,'
            ' and shot description into a single English FLUX image generation prompt.\n'
            'Requirements:\n'
            '- All characters must appear in the scene with correct spatial positions as described\n'
            '- Preserve every character appearance detail from the descriptions\n'
            '- Describe the environment, lighting, and camera angle precisely\n'
            '- English, under 150 words\n'
            '- Output ONLY the prompt text, no explanation'
        )
        user_text = (
            f'{char_section}\n\n'
            f'Scene setting: {scene_setting}\n'
            f'Shot description: {shot_desc}\n'
            f'Style: {style}' if style else ''
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_text},
        ]
        return self.chat(messages, model=self.models['prompt_opt'], temperature=0.5).strip()

    def review_shot(self, scene_desc: str, frame_paths: list,
                    model: str = None, character_ref: str = None) -> dict:
        """多模态审片：关键帧+场景描述→{score, dimensions, feedback, pass}

        v1: 若提供 character_ref，增加 character_consistency 维度（5维打分）。"""
        model = model or self.models['review']
        n_dims = 5 if character_ref else 4
        char_dim = (
            '\n5. character_consistency: 角色与参考图的一致性（外观/服装/毛色/体型）'
            if character_ref else ''
        )
        char_instr = (
            '\n第一张图是角色参考图，请对比后续关键帧中角色与参考图的一致性。'
            if character_ref else ''
        )
        system = (
            f'你是视频审片专家。根据给定的场景描述和关键帧截图，按{n_dims}个维度打分（每维1-10）：\n'
            '1. consistency: 画面内容与场景描述的匹配度\n'
            '2. quality: 画质（清晰度、色彩、构图）\n'
            '3. motion: 运动自然度、流畅度\n'
            '4. artifact: 无明显AI生成痕迹（畸形、融合、闪烁等）'
            f'{char_dim}\n'
            f'综合分={n_dims}维均值。≥7分通过。{char_instr}\n'
            '输出严格JSON格式：\n'
            '{"score": 8, "dimensions": {"consistency": 8, "quality": 9, "motion": 7, '
            '"artifact": 8' + (', "character_consistency": 8' if character_ref else '') +
            '}, "feedback": "具体反馈", "pass": true}'
        )
        content = [{'type': 'text', 'text': f'场景描述：{scene_desc}\n请按{n_dims}维度打分'}]
        if character_ref:
            b64 = self._image_to_base64(character_ref)
            content.append({'type': 'image_url', 'image_url': {'url': b64}})
        for path in frame_paths:
            b64 = self._image_to_base64(path)
            content.append({'type': 'image_url', 'image_url': {'url': b64}})

        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': content},
        ]
        return self.chat_json(messages, model=model, temperature=0.3, timeout=60, retries=1)

    def review_character(self, character_ref: str, preview_paths: list) -> dict:
        """角色锚质量审查：参考图+多角度预览→{score, pass, feedback}"""
        system = (
            '你是3D角色质量审查专家。根据角色参考图和多角度3D渲染预览，评估3D重建质量。\n'
            '按4个维度打分（每维1-10）：\n'
            '1. completeness: 角色完整性（无残缺、无悬浮碎片）\n'
            '2. multi_angle: 多角度稳定性（不同角度角色不崩坏）\n'
            '3. fidelity: 与参考图的一致性（外观/颜色/形态匹配）\n'
            '4. pose: 姿态自然度（角色应自然站立在地面上，而非躺倒、侧卧或悬浮在空中）\n'
            '综合分=四维均值。≥6分通过。\n'
            '输出严格JSON格式：\n'
            '{"score": 7, "dimensions": {"completeness": 7, "multi_angle": 7, '
            '"fidelity": 7, "pose": 7}, "feedback": "具体反馈", "pass": true}'
        )
        content = [{'type': 'text', 'text': '第一张是角色参考图，其余是多角度3D渲染预览，请评估3D重建质量。'}]
        b64 = self._image_to_base64(character_ref)
        content.append({'type': 'image_url', 'image_url': {'url': b64}})
        for path in preview_paths:
            b64 = self._image_to_base64(path)
            content.append({'type': 'image_url', 'image_url': {'url': b64}})

        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': content},
        ]
        return self.chat_json(messages, model=self.models['review'], temperature=0.3, timeout=60, retries=1)


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
