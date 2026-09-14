#!/usr/bin/env python3
"""图源命中率诊断 — 测多个图片源对不同概念返回图的准确率。

场景：营销号 pipeline 的 reference 图需要"概念→准图"。
必应 images 缩略图对模糊/实体概念返回不准（壁纸/雪山等无关图），
此脚本系统测 Bing / 图库 API / Commons / Openverse，看哪个源命中准。

用多模态 LLM 判"该图是否贴合概念"，统计每源命中率。
"""
import sys, os, re, json, time, html as H, urllib.request, urllib.parse, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.llm import LLMClient

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120'}
OUT = '/tmp/opencode/srctest'
os.makedirs(OUT, exist_ok=True)

CONCEPTS = ['复古收音机', '新款iPhone 18 Pro手机', '雪山日出的红色狐狸']
EN = {'复古收音机': 'vintage radio', '新款iPhone 18 Pro手机': 'iPhone 18 Pro',
      '雪山日出的红色狐狸': 'red fox mountain sunrise'}


def _get(url, headers=None, timeout=20, binary=False):
    req = urllib.request.Request(url, headers=headers or UA)
    r = urllib.request.urlopen(req, timeout=timeout).read()
    return r if binary else r.decode('utf-8', 'ignore')


def source_bing(concept_en, n=4):
    raw = _get(f'https://www.bing.com/images/search?q={urllib.parse.quote(concept_en)}')
    turls = set()
    for m in re.findall(r'm="({[^"]+)"', raw):
        try:
            d = json.loads(H.unescape(m))
        except Exception:
            continue
        if d.get('turl'):
            turls.add(d['turl'])
    return [('bing', u) for u in list(turls)[:n]]


def source_openverse(concept_en, n=4):
    # Openverse API 免 key，按相关性排序
    u = ('https://api.openverse.org/v1/images/?'
         f'q={urllib.parse.quote(concept_en)}&page_size={n}')
    try:
        d = json.loads(_get(u))
        urls = [r.get('url') for r in d.get('results', []) if r.get('url')]
        return [('openverse', u) for u in urls[:n]]
    except Exception as e:
        return [('openverse_err', repr(e)[:40])]


def source_commons(concept_en, n=4):
    u = ('https://commons.wikimedia.org/w/api.php?action=query'
         f'&generator=search&gsrsearch={urllib.parse.quote(concept_en)}'
         '&gsrnamespace=6&prop=imageinfo&iiprop=url&format=json&iiurlwidth=800')
    try:
        d = json.loads(_get(u))
        pages = d.get('query', {}).get('pages', {})
        urls = [p.get('imageinfo', [{}])[0].get('thumburl', '')
                for p in pages.values() if p.get('imageinfo')]
        return [('commons', u) for u in urls[:n]]
    except Exception as e:
        return [('commons_err', repr(e)[:40])]


def download(url):
    try:
        data = _get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=12, binary=True)
        if len(data) < 5000:
            return None
        ext = 'jpg' if data[:3] == b'\xff\xd8\xff' else ('png' if data[:8] == b'\x89PNG' else 'img')
        p = os.path.join(OUT, f'{int(time.time()*1000)}.{ext}')
        open(p, 'wb').write(data)
        return p
    except Exception:
        return None


def main():
    llm = LLMClient()
    for cn in CONCEPTS:
        print(f'\n{"="*60}\n概念: {cn}')
        ce = EN[cn]
        for src, items in [('bing', source_bing(ce)), ('openverse', source_openverse(ce)),
                           ('commons', source_commons(ce))]:
            hit, tot = 0, 0
            for tag, url in items:
                p = download(url)
                if not p:
                    continue
                tot += 1
                if tag.endswith('_err'):
                    ref_ok = False
                else:
                    try:
                        c = [{'type': 'text', 'text': f'这张图贴合概念「{cn}」吗？只回答 是 或 否'},
                             {'type': 'image_url', 'image_url': {'url': llm._image_to_base64(p)}}]
                        r = llm.chat([{'role': 'user', 'content': c}], model=llm.models['review'], timeout=40)
                        ref_ok = r.strip().startswith('是')
                    except Exception:
                        ref_ok = False
                if ref_ok:
                    hit += 1
            print(f'  {src:10s} 命中 {hit}/{tot}')


if __name__ == '__main__':
    main()
