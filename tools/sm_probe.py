#!/usr/bin/env python3
"""测干净图库源的匿名可用性 + 返回图是否贴合概念。

源：Pixabay(JSON RSS)、Unsplash(Source/API 匿名)、百度图片
概念："vintage radio" / "复古收音机"
输出每个源拿到几个可下载图 URL + 文件落到 /tmp/opencode/sm/ 供检查。
"""
import urllib.request, urllib.parse, json, re, os, sys

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120'}
OUT = '/tmp/opencode/sm'
os.makedirs(OUT, exist_ok=True)


def _get(url, headers=None, timeout=18, binary=False):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=headers or UA), timeout=timeout).read()
    return r if binary else r.decode('utf-8', 'ignore')


def pixabay(q, n=4):
    # Pixabay 匿名 JSON（无需 key 试）: 走 rss
    try:
        raw = _get(f'https://pixabay.com/rss/?q={urllib.parse.quote(q)}')
        urls = re.findall(r'<enclosure url="([^"]+)"', raw)
        return [('pixabay', u) for u in urls[:n]]
    except Exception as e:
        return [('pixabay_err', repr(e)[:60])]


def unsplash(q, n=4):
    # Unsplash 匿名无 key 不可直接搜；用 source.unsplash.com（镜像，未必稳）
    try:
        # 返回单图占位，尽量多取几个 via featured
        urls = [f'https://source.unsplash.com/featured/?{urllib.parse.quote(q)}' for _ in range(n)]
        return [('unsplash', u) for u in urls]
    except Exception as e:
        return [('unsplash_err', repr(e)[:60])]


def baidu(q, n=4):
    # 百度图片 page 接口（中文图）
    try:
        u = ('https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&word='
             f'{urllib.parse.quote(q)}&pn=0&rn={n}')
        raw = _get(u, headers={'Referer': 'https://image.baidu.com/', **UA})
        d = json.loads(raw)
        urls = [x.get('thumbURL') or x.get('middleURL') or '' for x in d.get('data', []) if x]
        urls = [u for u in urls if u]
        return [('baidu', u) for u in urls[:n]]
    except Exception as e:
        return [('baidu_err', repr(e)[:60])]


def grab(url):
    try:
        d = _get(url, binary=True)
        if len(d) < 5000:
            return None
        return d
    except Exception:
        return None


def main():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    concepts = ['vintage radio', '复古收音机']
    for c in concepts:
        print(f'\n==== 概念: {c} ====')
        for name, items in [('pixabay', pixabay(c)), ('unsplash', unsplash(c)), ('baidu', baidu(c))]:
            if len(items) == 1 and items[0][0].endswith('_err'):
                print(f'  {name:10s} ERR {items[0][1][:50]}')
                continue
            dl = 0
            for i, (tag, url) in enumerate(items):
                d = grab(url)
                if not d:
                    continue
                ext = 'jpg' if d[:3] == b'\xff\xd8\xff' else ('png' if d[:8] == b'\x89PNG' else 'img')
                p = f'{OUT}/{name}_{i}.{ext}'
                open(p, 'wb').write(d)
                dl += 1
            print(f'  {name:10s} 候选{len(items)} 可下载{dl}')


if __name__ == '__main__':
    main()
