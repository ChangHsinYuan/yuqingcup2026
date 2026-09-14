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

_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', 'config.json')


def _pexels_key():
    try:
        with open(_CFG_PATH) as f:
            return json.load(f).get('pexels', {}).get('api_key', '')
    except Exception:
        return ''

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
    """素材爬取器：参考图（Pexels 首选 / 必应兜底）+ BGM（incompetech）。"""

    @staticmethod
    def _pexels_search(keyword: str, top: int = 10) -> list:
        """Pexels 图库 API（精准返回对应版权图）→ [{murl, turl, md5:''}]。

        需 config.json pexels.api_key。Pexels 是英文图库：中文关键词先 LLM 英译再查。
        失败/无 key/无结果 返回 []（调用方回退必应）。
        """
        key = _pexels_key()
        if not key:
            return []
        q = keyword.strip()
        if any(ord(ch) > 127 for ch in q):
            q = AssetCrawler._en_translate(q) or q
        q = q.replace(' ', '+')
        u = f'https://api.pexels.com/v1/search?query={q}&per_page={max(top, 5)}&orientation=landscape'
        try:
            req = urllib.request.Request(u, headers={'Authorization': key, 'User-Agent': UA})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            return []
        out = []
        for ph in d.get('photos', []):
            src = ph.get('src', {})
            big = src.get('large2x') or src.get('large') or src.get('original') or ''
            med = src.get('medium') or big
            if big:
                out.append({'murl': big, 'turl': med, 'md5': ''})
        return out[:top]

    @staticmethod
    def _en_translate(text: str) -> str:
        """中文关键词 → 英文（供 Pexels 英文图库查询）。失败返回空串。"""
        try:
            from utils.llm import LLMClient
            llm = LLMClient()
            r = llm.chat([
                {'role': 'system', 'content': '把中文翻译成简洁的英文图片搜索词，只输出英文，如 复古收音机→vintage radio'},
                {'role': 'user', 'content': text},
            ], model=llm.models['prompt_opt'], timeout=40)
            return r.strip().split('\n')[0][:60]
        except Exception:
            return ''

    # ══ 1. 参考图搜索（Pexels 首选 + 必应兜底） ══
    def search_images(self, keyword: str, top: int = 40) -> list:
        """图片搜索 → [{murl(原图), turl(缩略图), md5}, ...]。

        **Pexels 首选**（图库 API，精准返回对应概念版权图，干净）；
        Pexels 无 key/失败时回退必应正式 images/search 页（聚合图源，可能有无关图）。
        """
        pex = self._pexels_search(keyword, top=top)
        if pex:
            return pex
        return self._search_bing(keyword, top=top)

    def _search_bing(self, keyword: str, top: int = 40) -> list:
        """必应正式 images/search 页面（聚合图源，返回 {murl,turl,md5}）。"""
        q = urllib.parse.quote(keyword)
        url = f'https://www.bing.com/images/search?q={q}&qft=+filterui:photo-photo'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            'Referer': 'https://www.bing.com/',
        })
        try:
            raw = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore')
        except Exception:
            return []
        out, seen = [], set()
        for m in re.findall(r'm="({[^"]+)"', raw):
            try:
                d = json.loads(H.unescape(m))
            except Exception:
                continue
            murl = d.get('murl') or ''
            turl = d.get('turl') or ''
            key = murl or turl
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({'murl': murl, 'turl': turl, 'md5': d.get('md5', '')})
        return out[:top]

    @staticmethod
    def _turl_enlarge(turl: str, width: int = 900, height: int = 700) -> str:
        """把必应 CDN 缩略图 URL 放大为高清原图（去 pid，加宽高）。

        必应 `th?id=OIP.<md5>` 是它缓存的原图，可通过去 pid + 指定 w/h 获取高清版
        （即用户在页面上点开放大看到的准图）。
        """
        if not turl:
            return ''
        s = turl.split('&pid=')[0]
        if '&w=' not in s:
            s += f'&w={width}&h={height}'
        return s

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
        **优先下载必应官方缩略图 turl**（真图 CDN，稳定且内容正确）；
        murl 第三方新闻直链内容不可靠（常为无关配图），仅作 turl 失败兜底。
        """
        os.makedirs(output_dir, exist_ok=True)
        cands = self.search_images(keyword, top=top * 4)
        out, seen_md5, tried = [], set(), []
        # 收集 (url, prio) — 必应 CDN 放大版(0) > CDN 原缩略图(1) > murl 来源页(2)
        for c in cands:
            if c.get('turl'):
                big = self._turl_enlarge(c['turl'])
                tried.append((big, 0))
                if big != c['turl']:
                    tried.append((c['turl'], 1))
            if c.get('murl'):
                tried.append((c['murl'], 2))
        tried = sorted(set(tried), key=lambda x: x[1])
        for url, _prio in tried:
            if len(out) >= top:
                break
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
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
            out.append({'path': os.path.abspath(path), 'url': url, 'size': len(data)})
        return out

    # ══ 2. LLM concept → 图片搜索关键词 ══
    def extract_keywords(self, concept: str, llm=None, n: int = 3) -> list:
        """LLM 从 concept 提取 n 个图片搜索关键词（无 llm 时退化为 concept 原文）。

        提示词强调产出"视觉高清图/产品图/场景照片"式搜索词，保留具体实体名
        （如 iPhone 18 Pro、具体地名、具体赛事），避免衍生成新闻短语导致搜不到图。
        """
        if llm is None:
            return [concept]
        prompt = [
            {'role': 'system', 'content':
             '你是图片搜索助手。把视频概念转成最适合"图片搜索引擎找高清图片"的中文搜索词。\n'
             '要求：\n'
             '- 必须完整保留概念里的具体实体名（产品型号如 iPhone 18 Pro、地名、赛事名、人名、动物/物件），不能改写成新闻短语\n'
             '- 面向图片搜索：尽量是"物件/场景/实景"类，可加"高清/实拍/摄影"等视觉词\n'
             '- 不要用"争议/价格/谁买单"这类无画面感的新闻词\n'
             '只输出 JSON 数组，如 ["iPhone 18 Pro 高清实拍图", "iPhone 18 Pro 手机 实物图", "iPhone 18 Pro 背面 摄影"]，共 3 个。'},
            {'role': 'user', 'content': f'视频概念: {concept}\n生成 {n} 个图片搜索关键词'},
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
        """concept → LLM 关键词 → 逐词搜索下载（每词 top 张）。

        修复：先搜原始 concept（保住具体实体名精确图，避免 LLM 衍生词绕开），
        再补 LLM 生成的可视化关键词。
        """
        kws = [concept] + [k for k in self.extract_keywords(concept, llm=llm) if k != concept]
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
