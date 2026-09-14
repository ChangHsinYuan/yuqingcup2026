#!/usr/bin/env python3
"""素材爬取 — 参考图搜索下载 + BGM 免版权音乐爬取（v4 M5）。

两个素材源（人声样本已在 voice_clone.py M3 完成）：
  - 参考图：必应图片搜索（cn.bing.com，直连可达，原图直链）→ 下载到任务参考图目录
  - BGM：incompetech.com（Kevin MacLeod CC BY，直连可达）按 feel 标签搜曲 → 下载 → loudnorm → bgm/{mood}.wav

用法:
  from utils.asset_crawler import AssetCrawler
  ac = AssetCrawler()
  ac.extract_keywords('深海里发光的水母')          # LLM concept → 图片搜索关键词
  ac.download_images('深海水母', 'output/ref/')   # 搜索+下载+校验+去重
  ac.crawl_bgm('epic')                            # 按 mood 爬 BGM → bgm/epic.wav

CLI:
  python utils/asset_crawler.py keywords "概念"
  python utils/asset_crawler.py images "关键词" -o output/ref/ --top 5
  python utils/asset_crawler.py bgm epic --max-dur 180
"""
import os
import re
import sys
import json
import hashlib
import shutil
import html as H
import subprocess
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# 标准 mood → incompetech feel 搜索词（可在 CLI 覆盖）
MOOD_FEEL = {
    'calm': ['calm', 'gentle', 'peaceful'],
    'uplifting': ['uplifting', 'optimistic', 'hopeful'],
    'mysterious': ['mysterious', 'dark', 'tense'],
    'dramatic': ['dramatic', 'epic', 'adventure'],
    'playful': ['playful', 'quirky', 'funny'],
    'epic': ['epic', 'heroic', 'triumphant'],
    'sad': ['sad', 'melancholy', 'emotional'],
    'tense': ['tense', 'suspense', 'thriller'],
}


class AssetCrawler:
    """素材爬取器：参考图（必应）+ BGM（incompetech）。"""

    # ══ 1. 参考图搜索（必应图片 async 接口） ══
    def search_images(self, keyword: str, top: int = 10) -> list:
        """必应图片搜索 → [{murl(原图), turl(缩略图)}, ...]（可能多于 top，调用方自取）。"""
        q = urllib.parse.quote(keyword)
        url = f'https://cn.bing.com/images/async?q={q}&first=0&count={max(top, 35)}&mmasync=1'
        req = urllib.request.Request(url, headers={'User-Agent': UA,
                                                   'Referer': 'https://cn.bing.com/images'})
        raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', 'ignore')
        out, seen = [], set()
        for m in re.findall(r'm="({[^"]+)"', raw):
            try:
                d = json.loads(H.unescape(m))
            except Exception:
                continue
            murl = d.get('murl') or ''
            if not murl or murl in seen:
                continue
            seen.add(murl)
            out.append({'murl': murl, 'turl': d.get('turl', '')})
            if len(out) >= top:
                break
        return out

    @staticmethod
    def _magic_ok(data: bytes) -> bool:
        """图片 magic bytes 校验（JPEG/PNG/WebP/GIF/BMP）。"""
        return (data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n'
                or data[:4] == b'RIFF' and data[8:12] == b'WEBP'
                or data[:3] == b'GIF' or data[:2] == b'BM')

    def download_images(self, keyword: str, output_dir: str, top: int = 10,
                        min_bytes: int = 8192) -> list:
        """搜索 + 下载原图 + magic bytes 校验 + md5 去重 → [{path, url, size}, ...]。

        候选按序尝试（部分源站 403/超时），成功 top 张即停。
        """
        os.makedirs(output_dir, exist_ok=True)
        cands = self.search_images(keyword, top=top * 3)
        out, seen_md5 = [], set()
        for c in cands:
            if len(out) >= top:
                break
            try:
                req = urllib.request.Request(c['murl'], headers={'User-Agent': UA})
                data = urllib.request.urlopen(req, timeout=15).read()
            except Exception:
                continue
            if len(data) < min_bytes or not self._magic_ok(data):
                continue
            md5 = hashlib.md5(data).hexdigest()
            if md5 in seen_md5:
                continue
            seen_md5.add(md5)
            ext = '.jpg'
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                ext = '.png'
            elif data[:4] == b'RIFF':
                ext = '.webp'
            path = os.path.join(output_dir, f'{keyword[:16]}_{len(out)+1}{ext}')
            with open(path, 'wb') as f:
                f.write(data)
            out.append({'path': os.path.abspath(path), 'url': c['murl'], 'size': len(data)})
        return out

    # ══ 2. LLM concept → 图片搜索关键词 ══
    def extract_keywords(self, concept: str, llm=None, n: int = 3) -> list:
        """LLM 从 concept 提取 n 个图片搜索关键词（无 llm 时退化为 concept 原文）。"""
        if llm is None:
            return [concept]
        prompt = [
            {'role': 'system', 'content':
             '你是图片搜索助手。把视频概念转成适合搜索引擎找参考图的中文关键词。'
             '只输出 JSON 数组，如 ["关键词1","关键词2","关键词3"]，关键词要具体、有画面感。'},
            {'role': 'user', 'content': f'视频概念: {concept}\n生成 {n} 个搜索关键词'},
        ]
        try:
            r = llm.chat_json(prompt)
            kws = r if isinstance(r, list) else (r.get('keywords') or [])
            kws = [str(k).strip() for k in kws if str(k).strip()][:n]
            return kws or [concept]
        except Exception as e:
            print(f'  ⚠ LLM 关键词提取失败: {e}，用 concept 原文')
            return [concept]

    def crawl_concept_refs(self, concept: str, output_dir: str, top: int = 5,
                           llm=None) -> list:
        """concept → LLM 关键词 → 逐词搜索下载（每词 top 张）。"""
        kws = self.extract_keywords(concept, llm=llm)
        out = []
        for kw in kws:
            print(f'  [crawl] 搜索 "{kw}" ...')
            items = self.download_images(kw, output_dir, top=top)
            print(f'  [crawl] {kw}: {len(items)} 张')
            for it in items:
                it['keyword'] = kw
            out.extend(items)
        return out

    # ══ 3. BGM 爬取（incompetech CC BY） ══
    @staticmethod
    def _parse_length(s) -> float:
        """incompetech pieces.json 的 length（'HH:MM:SS'）→ 秒。"""
        try:
            parts = [float(x) for x in str(s).split(':')]
            sec = 0.0
            for p in parts:
                sec = sec * 60 + p
            return sec
        except Exception:
            return 0.0

    def search_bgm(self, mood: str, max_dur: float = 180.0, top: int = 5) -> list:
        """按 mood 搜曲 → [{filename, title, feel, duration, genre}, ...] 按时长偏好排序。

        pieces.json 来自 incompetech.com/music/royalty-free/pieces.json（缓存于 bgm/）。
        """
        pieces_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'bgm', 'pieces.json')
        with open(pieces_path) as f:
            pieces = json.load(f)
        feels = MOOD_FEEL.get(mood.lower(), [mood.lower()])
        scored = []
        for p in pieces:
            feel = (p.get('feel') or '').lower()
            dur = self._parse_length(p.get('length'))
            if dur <= 0 or dur > max_dur:
                continue
            hit = sum(1 for fw in feels if fw in feel)
            if hit <= 0:
                continue
            # 时长越接近 60s 越好（避免 200MB 巨物 + 太短不够用）
            scored.append((abs(dur - 60.0), -hit, p))
        scored.sort(key=lambda x: (x[0], x[1]))
        out = []
        for _, _, p in scored[:top]:
            out.append({'filename': p.get('filename'), 'title': p.get('title'),
                        'feel': p.get('feel'), 'duration': self._parse_length(p.get('length')),
                        'genre': p.get('genre')})
        return out

    def crawl_bgm(self, mood: str, max_dur: float = 180.0, output: str = None) -> str:
        """按 mood 爬曲 → 下载 mp3 → loudnorm I=-20 → bgm/{mood}.wav。返回输出路径。"""
        cands = self.search_bgm(mood, max_dur=max_dur, top=5)
        if not cands:
            raise RuntimeError(f'没有匹配 "{mood}" 的曲目（feel 搜索词: {MOOD_FEEL.get(mood, [mood])}）')
        bgm_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'bgm')
        tmp_mp3 = os.path.join(bgm_dir, f'.crawl_{mood}.mp3')
        out = output or os.path.join(bgm_dir, f'{mood}.wav')
        for c in cands:
            # filename 自带 .mp3 后缀（"Cretaceous Dawn.mp3"），直接拼不可再加
            url = (f'https://incompetech.com/music/royalty-free/'
                   f'mp3-royaltyfree/{urllib.parse.quote(c["filename"])}')
            print(f'  [bgm] {c["title"]} ({c["duration"]:.0f}s, feel={c["feel"]}) ...')
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                data = urllib.request.urlopen(req, timeout=120).read()
                with open(tmp_mp3, 'wb') as f:
                    f.write(data)
            except Exception as e:
                print(f'  [bgm] 下载失败: {e}，试下一首')
                continue
            cmd = [FFMPEG, '-y', '-loglevel', 'error', '-i', tmp_mp3,
                   '-af', 'loudnorm=I=-20:TP=-1.5:LRA=11',
                   '-ar', '44100', '-ac', '2', out]
            subprocess.run(cmd, capture_output=True, timeout=600)
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                os.remove(tmp_mp3)
                print(f'  [bgm] OK -> {out}')
                return os.path.abspath(out)
            print(f'  [bgm] 转码失败，试下一首')
        raise RuntimeError(f'"{mood}" 所有候选曲目下载/转码均失败')


def main():
    import argparse
    base = argparse.ArgumentParser(description='素材爬取（参考图 + BGM）')
    sub = base.add_subparsers(dest='cmd', required=True)

    p_k = sub.add_parser('keywords', help='LLM concept → 图片搜索关键词')
    p_k.add_argument('concept')
    p_k.add_argument('-n', type=int, default=3)

    p_i = sub.add_parser('images', help='必应图片搜索下载')
    p_i.add_argument('keyword')
    p_i.add_argument('-o', '--output', required=True, help='输出目录')
    p_i.add_argument('--top', type=int, default=5)

    p_c = sub.add_parser('refs', help='concept → 关键词 → 图片下载（一条龙）')
    p_c.add_argument('concept')
    p_c.add_argument('-o', '--output', required=True)
    p_c.add_argument('--top', type=int, default=3, help='每关键词张数')

    p_b = sub.add_parser('bgm', help='按 mood 爬 BGM → bgm/{mood}.wav')
    p_b.add_argument('mood')
    p_b.add_argument('--max-dur', type=float, default=180.0)
    p_b.add_argument('--list', action='store_true', help='只列候选不下载')

    a = base.parse_args()
    ac = AssetCrawler()

    if a.cmd == 'keywords':
        from utils.llm import LLMClient
        print(ac.extract_keywords(a.concept, llm=LLMClient(), n=a.n))

    elif a.cmd == 'images':
        items = ac.download_images(a.keyword, a.output, top=a.top)
        for it in items:
            print(f'{it["path"]}  {it["size"]//1024}KB  {it["url"][:80]}')
        print(f'共 {len(items)} 张')

    elif a.cmd == 'refs':
        from utils.llm import LLMClient
        items = ac.crawl_concept_refs(a.concept, a.output, top=a.top, llm=LLMClient())
        for it in items:
            print(f'[{it["keyword"]}] {it["path"]}  {it["size"]//1024}KB')
        print(f'共 {len(items)} 张')

    elif a.cmd == 'bgm':
        if a.list:
            for c in ac.search_bgm(a.mood, max_dur=a.max_dur):
                print(f'{c["title"]}  {c["duration"]:.0f}s  feel={c["feel"]}  file={c["filename"]}')
        else:
            ac.crawl_bgm(a.mood, max_dur=a.max_dur)


if __name__ == '__main__':
    main()
