#!/usr/bin/env python3
"""FunClip 智能裁剪 — 长素材（长旁白/长视频）语义裁剪。

基于 FunASR（阿里中文 ASR）转写 + 字级时间戳，按 LLM 语义判断保留/删除片段，
再用 ffmpeg 裁剪拼接。解决两个场景：
  - 长旁白录制有口误 → 按语义去口误段
  - 爬取长视频素材 → 按内容语义提取关键段

用法:
  from utils.funclip import FunClip
  fc = FunClip(device='cuda:0')
  chars = fc.transcribe('long.wav')          # 字级时间戳
  fc.clip_segments('long.wav', [(0,3),(5,8)], 'out.wav')   # 按时段裁剪
  fc.smart_clip('long.wav', '只要讲'xx'的部分', 'out.wav', llm=llm)  # LLM 语义裁剪

CLI:
  python utils/funclip.py transcribe long.wav
  python utils/funclip.py clip long.wav --segments "0-3,5-8" -o out.wav
  python utils/funclip.py smart long.wav -i "保留关键段" -o out.wav
"""
import os
import sys
import json
import subprocess
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 碎片化显存分配（多服务共用 GPU 时默认 chunk 策略可能 OOM，即使显存充足）
os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
FFPROBE = shutil.which('ffprobe') or 'ffprobe'

# FunASR 官方带时间戳的中英混合模型（vad-punc-asr_nat，AsyncTokenizer 组合）
MODEL = 'iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch'


class FunClip:
    """FunASR 智能裁剪器：ASR 转写 + 字级时间戳 + 语义裁剪。"""

    def __init__(self, device: str = 'auto', model: str = MODEL):
        self.device = device
        self.model = None
        self.model_name = model

    @staticmethod
    def auto_device() -> str:
        """选当前空闲显存最多的 GPU（4 卡共服务，硬编码易撞满卡）。

        逐卡 try/except：满卡连 CUDA context 都建不出来（cudaMemGetInfo OOM），
        必须跳过而不是让整个进程死掉。
        """
        try:
            import torch
            best, best_free = None, -1
            for i in range(torch.cuda.device_count()):
                try:
                    free, _ = torch.cuda.mem_get_info(i)
                except Exception:
                    continue
                if free > best_free:
                    best, best_free = i, free
            if best is not None:
                return f'cuda:{best}'
        except Exception:
            pass
        return 'cuda:1'

    # ── 懒加载模型（与 stt.py 缓存一致） ──
    def _get_model(self):
        if self.model is None:
            from funasr import AutoModel
            device = self.auto_device() if self.device == 'auto' else self.device
            self.model = AutoModel(
                model=self.model_name,
                device=device,
                disable_update=True,
            )
        return self.model

    # ══ 1. 转写：字级时间戳 ══
    def transcribe(self, audio_path: str, min_char_ms: int = 0):
        """转写音频 → 字级时间戳列表 [{char, start_ms, end_ms}, ...]。

        用 return_raw=True + batch_size_s 触发 FunASR 返回字级 'timestamp'。
        时间戳单位 ms。
        """
        m = self._get_model()
        res = m.generate(input=audio_path, batch_size_s=300, return_raw=True, )
        out = []
        for r in res:
            raw_text = r.get('text', '')
            chars = raw_text.split(' ')          # 字级文本以空格分隔
            ts = r.get('timestamp') or []
            # 逐字对齐（文本与时间戳一一对应）
            for i, ch in enumerate(chars):
                if not ch.strip():
                    continue
                start_ms = ts[i][0] if i < len(ts) else None
                end_ms = ts[i][1] if i < len(ts) else None
                if start_ms is None or end_ms is None:
                    continue
                if min_char_ms and (end_ms - start_ms) < min_char_ms:
                    continue
                out.append({'char': ch, 'start_ms': start_ms, 'end_ms': end_ms})
        return out

    # ══ 2. 语句聚合：字级时间戳合并成句（按标点/静音） ══
    def to_sentences(self, chars, gap_ms: int = 300, max_gap_ms: int = 1500):
        """把字级时间戳聚合成句子（近似由标点或长间隔切分）。

        简化：按字间间隔 gap>gap_ms 或累计超 max_gap_ms 切分。
        """
        if not chars:
            return []
        sentences = []
        cur_start = chars[0]['start_ms']
        cur_end = chars[0]['end_ms']
        cur_text = chars[0]['char']
        for prev, cur in zip(chars, chars[1:]):
            interval = cur['start_ms'] - prev['end_ms']
            if interval > gap_ms or (cur['start_ms'] - cur_start) > max_gap_ms:
                sentences.append({'text': cur_text, 'start_ms': cur_start, 'end_ms': cur_end})
                cur_start = cur['start_ms']
                cur_text = ''
            cur_text += cur['char']
            cur_end = cur['end_ms']
        sentences.append({'text': cur_text, 'start_ms': cur_start, 'end_ms': cur_end})
        return sentences

    # ══ 3. 按时段裁剪拼接（ffmpeg） ══
    def _has_video(self, input_path: str) -> bool:
        """探测输入是否有视频流（区分 视频/纯音频 素材）。"""
        r = subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', 'v', '-show_entries',
             'stream=codec_type', '-of', 'csv=p=0', input_path],
            capture_output=True, text=True, timeout=30)
        return 'video' in r.stdout

    def clip_segments(self, input_path: str, segments, output_path: str,
                      ignore_errors: bool = False):
        """按时间段 [ (start_s, end_s), ... ] 裁剪拼接。

        segments: list of (start_seconds, end_seconds)
        自动探测输入是视频（保留画面）还是纯音频（仅音频），输出格式随之匹配。
        """
        if not segments:
            raise ValueError('segments is empty')
        has_video = self._has_video(input_path)
        ext = '.mp4' if has_video else '.wav'
        tmpdir = output_path + '_tmp'
        os.makedirs(tmpdir, exist_ok=True)
        seg_files = []
        list_file = os.path.join(tmpdir, 'list.txt')
        try:
            for i, (s, e) in enumerate(segments):
                seg = os.path.join(tmpdir, f'seg_{i:03d}{ext}')
                dur = max(0.0, float(e) - float(s))
                cmd = [FFMPEG, '-y', '-ss', f'{float(s):.3f}', '-i', input_path, '-t', f'{dur:.3f}']
                if has_video:
                    cmd += ['-map', '0:v:0', '-map', '0:a:0?',
                            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                            '-pix_fmt', 'yuv420p', '-profile:v', 'high',
                            '-c:a', 'aac', '-b:a', '192k', seg]
                else:
                    cmd += ['-map', '0:a:0?', '-c:a', 'pcm_s16le', seg]
                subprocess.run(cmd, capture_output=True, timeout=600)
                if os.path.isfile(seg) and os.path.getsize(seg) > 0:
                    seg_files.append(seg)
                    with open(list_file, 'a') as f:
                        f.write(f"file '{os.path.abspath(seg)}'\n")
                elif not ignore_errors:
                    raise RuntimeError(f'seg {s}-{e} 裁剪失败')
            if not seg_files:
                raise RuntimeError('无可裁剪片段')
            cmd = [
                FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                '-c', 'copy', output_path,
            ]
            if os.path.exists(output_path):
                os.remove(output_path)
            subprocess.run(cmd, capture_output=True, timeout=600)
            return output_path
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ══ 4. LLM 语义裁剪（M4 主功能） ══
    def smart_clip(self, input_path: str, instructions: str, output_path: str,
                   llm=None, pause_ms: int = 400) -> dict:
        """按 LLM 语义判断保留/删除 → 裁剪拼接。

        流程：转写 → 聚合成句 → LLM 逐句判定 keep/drop → 裁剪保留段。
        llm: 提供 .chat_json(messages) 的 LLM 客户端（复用 utils.llm.LLMClient）
        instructions: 用户保存/删除语义描述（如 "只要讲龙的画面，去掉口误")
        """
        import time
        t0 = time.time()
        # 1) 转写(秒) + 聚合
        chars = self.transcribe(input_path)
        sents_sec = [{'text': s['text'], 'start': s['start_ms']/1000.0,
                      'end': s['end_ms']/1000.0} for s in self.to_sentences(chars)]
        if not sents_sec:
            raise RuntimeError('ASR 没有转写出内容')

        # 2) LLM 判定（无 llm 则全保留）
        keep = [True] * len(sents_sec)
        if llm is not None:
            prompt = [
                {'role': 'system', 'content':
                 '你是视频剪辑助理。下面是一段旁白的句子列表(带时间), '
                 '根据用户指令判断每句是否保留。只输出 JSON数组, 如 [{"idx":0,"keep":true,"reason":"..."}]'},
                {'role': 'user', 'content':
                 f"用户指令: {instructions}\n句子:\n" +
                 '\n'.join(f"{i}: [{s['start']:.1f}-{s['end']:.1f}] {s['text']}"
                            for i, s in enumerate(sents_sec))},
            ]
            try:
                decisions = llm.chat_json(prompt)
                if isinstance(decisions, dict):
                    decisions = decisions.get('decisions') or decisions.get('result') or []
                for d in decisions:
                    idx = int(d.get('idx', -1))
                    if 0 <= idx < len(keep):
                        keep[idx] = bool(d.get('keep', True))
            except Exception as e:
                print(f'  ⚠ LLM 语义裁剪判定失败: {e}，保留全部')

        # 3) 合并连续保留段 → 裁剪
        segments = []
        i = 0
        n = len(sents_sec)
        while i < n:
            if keep[i]:
                s = sents_sec[i]['start']
                e = sents_sec[i]['end']
                j = i + 1
                while j < n and keep[j]:
                    e = sents_sec[j]['end']
                    j += 1
                # 吸收句间 pause（保留段之间的少量静音，防生硬）
                if e < n and j < n and sents_sec[j]['start'] - e < pause_ms:
                    e = sents_sec[j]['start']
                segments.append((s, e))
                i = j
            else:
                i += 1

        # 4) 执行裁剪
        if not segments:
            raise RuntimeError('LLM 判定全部删除，无保留片段')
        self.clip_segments(input_path, segments, output_path)

        return {
            'segments': segments,
            'sentences': sents_sec,
            'keep': [b for b in keep],
            'duration_ms': int((time.time() - t0) * 1000),
        }

    # ══ 工具：时长 ══
    @staticmethod
    def duration(path: str) -> float:
        r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        try:
            return float(r.stdout.strip())
        except ValueError:
            return 0.0


def main():
    import argparse
    # 构建子命令
    base = argparse.ArgumentParser(description='FunClip 智能裁剪 (FunASR)')
    sub = base.add_subparsers(dest='cmd', required=True)

    p_t = sub.add_parser('transcribe', help='ASR 转写显示字级时间戳')
    p_t.add_argument('audio')
    p_t.add_argument('--device', default='auto')
    p_t.add_argument('--sentences', action='store_true', help='聚合成句显示')

    p_c = sub.add_parser('clip', help='按时间段裁剪拼接')
    p_c.add_argument('input')
    p_c.add_argument('--segments', required=True, help='如 "0-3,5-8" (秒)')
    p_c.add_argument('-o', '--output', required=True)
    p_c.add_argument('--device', default='auto')

    p_s = sub.add_parser('smart', help='LLM 语义裁剪')
    p_s.add_argument('input')
    p_s.add_argument('-i', '--instructions', required=True, help='保留/删除语义')
    p_s.add_argument('-o', '--output', required=True)
    p_s.add_argument('--device', default='auto')
    p_s.add_argument('--no-llm', action='store_true', help='跳过 LLM 全保留')

    a = base.parse_args()
    fc = FunClip(device=a.device)

    if a.cmd == 'transcribe':
        chars = fc.transcribe(a.audio)
        if a.sentences:
            for s in fc.to_sentences(chars):
                print(f"  [{s['start_ms']/1000:.2f}-{s['end_ms']/1000:.2f}] {s['text']}")
        else:
            print(''.join(c['char'] for c in chars))
            print(f"共 {len(chars)} 字, 首字 {chars[0]['start_ms']}ms, 末字 {chars[-1]['end_ms']}ms" if chars else '无内容')

    elif a.cmd == 'clip':
        segs = []
        for part in a.segments.split(','):
            s, e = part.split('-')
            segs.append((float(s), float(e)))
        fc.clip_segments(a.input, segs, a.output)
        print(f'OK 裁剪 {len(segs)} 段 -> {a.output}')

    elif a.cmd == 'smart':
        llm = None
        if not a.no_llm:
            try:
                from utils.llm import LLMClient
                llm = LLMClient()
            except Exception as e:
                print(f'  ⚠ LLMClient 不可用: {e}')
        r = fc.smart_clip(a.input, a.instructions, a.output, llm=llm)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:800])


if __name__ == '__main__':
    main()
