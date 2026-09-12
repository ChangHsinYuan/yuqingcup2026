#!/usr/bin/env python3
"""热点爬虫 — 多源抓取中文热点话题

用法:
  from utils.crawler import Crawler
  crawler = Crawler()
  topics = crawler.fetch_hot_topics()
  for t in topics[:10]:
      print(f'[{t["source"]}] {t["title"]} (heat={t["heat"]})')

  # 指定源
  topics = crawler.fetch_hot_topics(sources=['bilibili', 'weibo'])

  # CLI
  python utils/crawler.py --top 20
  python utils/crawler.py --source bilibili --top 10
"""
import json
import os
import time
import random
import urllib.request
import urllib.error

import requests
from bs4 import BeautifulSoup

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')

DEFAULT_SOURCES = ['bilibili', 'weibo', 'zhihu', 'baidu']
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
]


def _random_ua():
    return random.choice(USER_AGENTS)


def _get(url, headers=None, timeout=15, encoding=None):
    """带 UA 轮换的 HTTP GET"""
    h = {'User-Agent': _random_ua()}
    if headers:
        h.update(headers)
    resp = requests.get(url, headers=h, timeout=timeout)
    if encoding:
        resp.encoding = encoding
    return resp


class Crawler:
    """多源热点爬虫"""

    def fetch_hot_topics(self, sources=None, top_per_source=20) -> list:
        """从多个源抓取热点话题

        Args:
            sources: 源列表，None=全部 ['bilibili','weibo','zhihu','baidu']
            top_per_source: 每个源取前 N 条

        Returns:
            [{title, source, heat, url, snippet, rank}]
        """
        if sources is None:
            sources = DEFAULT_SOURCES

        all_topics = []
        for source in sources:
            fetcher = getattr(self, f'_fetch_{source}', None)
            if not fetcher:
                print(f'  ⚠ unknown source: {source}')
                continue
            try:
                print(f'  [{source}] 抓取中...')
                topics = fetcher()
                topics = topics[:top_per_source]
                all_topics.extend(topics)
                print(f'  [{source}] ✅ {len(topics)} 条')
            except Exception as e:
                print(f'  [{source}] ❌ {e}')

        # 按 source 内 rank 排序，不跨源排序（不同源 heat 不可比）
        return all_topics

    def _fetch_bilibili(self) -> list:
        """B站热门视频 — 官方 API，无需认证"""
        url = 'https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1'
        resp = _get(url, headers={'Referer': 'https://www.bilibili.com/'})
        data = resp.json()
        if data.get('code') != 0:
            raise RuntimeError(f'B站 API 错误: {data.get("message", "?")}')

        topics = []
        for i, item in enumerate(data['data']['list']):
            owner = item.get('owner', {}).get('name', '')
            stat = item.get('stat', {})
            topics.append({
                'title': item['title'],
                'source': 'bilibili',
                'heat': stat.get('view', 0),
                'url': f'https://www.bilibili.com/video/{item["bvid"]}',
                'snippet': f'UP主:{owner} | 播放:{stat.get("view", 0)} | 弹幕:{stat.get("danmaku", 0)}',
                'rank': i + 1,
                'extra': {
                    'tname': item.get('tname', ''),
                    'duration': item.get('duration', 0),
                    'up': owner,
                },
            })
        return topics

    def _fetch_weibo(self) -> list:
        """微博热搜 — 网页抓取"""
        url = 'https://weibo.com/ajax/side/hotSearch'
        resp = _get(url, headers={'Referer': 'https://weibo.com'})
        data = resp.json()
        items = data.get('data', {}).get('realtime', [])

        topics = []
        for i, item in enumerate(items):
            label = item.get('label_name', '')
            note = item.get('note', item.get('word', ''))
            num = item.get('num', 0)
            topics.append({
                'title': note,
                'source': 'weibo',
                'heat': num,
                'url': f'https://s.weibo.com/weibo?q={note}',
                'snippet': f'热搜{"🔥" if label else ""} | 热度:{num}',
                'rank': i + 1,
                'extra': {'label': label, 'category': item.get('category', '')},
            })
        return topics

    def _fetch_zhihu(self) -> list:
        """知乎热榜 — API"""
        url = 'https://api.zhihu.com/topstory/hot-list?limit=20&reverse_order=0'
        try:
            resp = _get(url, headers={'Referer': 'https://www.zhihu.com/'})
            data = resp.json()
            items = data.get('data', [])

            topics = []
            for i, item in enumerate(items):
                target = item.get('target', {})
                title = target.get('title', '')
                excerpt = target.get('excerpt', '')
                heat = item.get('detail_text', '')
                topics.append({
                    'title': title,
                    'source': 'zhihu',
                    'heat': i + 1,
                    'url': f'https://www.zhihu.com/question/{target.get("id", "")}',
                    'snippet': excerpt[:80] if excerpt else '',
                    'rank': i + 1,
                    'extra': {'heat_text': heat},
                })
            return topics
        except Exception:
            return self._fetch_zhihu_fallback()

    def _fetch_zhihu_fallback(self) -> list:
        """知乎热榜 — 网页抓取兜底"""
        url = 'https://www.zhihu.com/hot'
        resp = _get(url, encoding='utf-8')
        soup = BeautifulSoup(resp.text, 'lxml')
        items = soup.select('.HotList-item')

        topics = []
        for i, item in enumerate(items[:20]):
            title_el = item.select_one('.HotList-itemTitle')
            excerpt_el = item.select_one('.HotList-itemExcerpt')
            title = title_el.get_text(strip=True) if title_el else ''
            excerpt = excerpt_el.get_text(strip=True) if excerpt_el else ''
            if not title:
                continue
            topics.append({
                'title': title,
                'source': 'zhihu',
                'heat': i + 1,
                'url': '',
                'snippet': excerpt[:80],
                'rank': i + 1,
            })
        return topics

    def _fetch_baidu(self) -> list:
        """百度热搜 — 网页抓取"""
        url = 'https://top.baidu.com/board?tab=realtime'
        resp = _get(url, encoding='utf-8')
        soup = BeautifulSoup(resp.text, 'lxml')

        items = soup.select('.c-single-text-clip')
        topics = []
        seen = set()
        rank = 0
        for item in items:
            title = item.get_text(strip=True)
            if not title or title in seen:
                continue
            seen.add(title)
            rank += 1
            parent = item.find_parent('div', class_='category-wrap_iQLoo')
            snippet = ''
            heat_text = ''
            if parent:
                desc = parent.select_one('.hot-desc_1kjb24')
                if desc:
                    snippet = desc.get_text(strip=True)[:80]
                heat_el = parent.select_one('.hot-index_1Bl1a')
                if heat_el:
                    heat_text = heat_el.get_text(strip=True)

            link = item.find('a')
            href = link.get('href', '') if link else ''

            topics.append({
                'title': title,
                'source': 'baidu',
                'heat': rank,
                'url': href,
                'snippet': snippet,
                'rank': rank,
                'extra': {'heat_text': heat_text},
            })
            if rank >= 20:
                break
        return topics

    def fetch_youtube_trending(self, region='CN', top=20) -> list:
        """YouTube 热门 — yt-dlp 元数据（无下载）

        注意：需要能访问 YouTube（可能需代理）
        """
        import subprocess
        cmd = [
            'yt-dlp', '--flat-playlist', '--dump-json',
            f'--playlist-end', str(top),
            f'https://www.youtube.com/feed/trending?gl={region}',
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        topics = []
        for i, line in enumerate(result.stdout.strip().split('\n')):
            if not line:
                continue
            try:
                item = json.loads(line)
                topics.append({
                    'title': item.get('title', ''),
                    'source': 'youtube',
                    'heat': item.get('view_count', 0),
                    'url': item.get('url', ''),
                    'snippet': f'频道:{item.get("uploader", "?")} | 时长:{item.get("duration", 0)}s',
                    'rank': i + 1,
                    'extra': {
                        'uploader': item.get('uploader', ''),
                        'duration': item.get('duration', 0),
                    },
                })
            except json.JSONDecodeError:
                continue
        return topics

    def save_topics(self, topics, output_path):
        """保存热点到 JSON 文件"""
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'count': len(topics),
                'topics': topics,
            }, f, ensure_ascii=False, indent=2)
        print(f'  saved: {output_path} ({len(topics)} topics)')

    def fetch_rss(self, rss_url, source_name='rss', top=20) -> list:
        """通用 RSS 订阅抓取（feedparser）

        Args:
            rss_url: RSS feed URL
            source_name: 源名称
            top: 取前 N 条
        """
        import feedparser
        feed = feedparser.parse(rss_url, request_headers={'User-Agent': _random_ua()})

        topics = []
        for i, entry in enumerate(feed.entries[:top]):
            topics.append({
                'title': entry.get('title', ''),
                'source': source_name,
                'heat': len(feed.entries) - i,
                'url': entry.get('link', ''),
                'snippet': entry.get('summary', '')[:120] if entry.get('summary') else '',
                'rank': i + 1,
                'extra': {
                    'published': entry.get('published', ''),
                    'author': entry.get('author', ''),
                },
            })
        return topics


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='热点爬虫')
    parser.add_argument('--source', '-s', default='all', help='源: all/bilibili/weibo/zhihu/baidu/youtube')
    parser.add_argument('--top', '-n', type=int, default=20, help='每源取前 N 条')
    parser.add_argument('--output', '-o', default=None, help='输出 JSON 路径')
    args = parser.parse_args()

    crawler = Crawler()

    if args.source == 'all':
        topics = crawler.fetch_hot_topics(top_per_source=args.top)
    elif args.source == 'youtube':
        topics = crawler.fetch_youtube_trending(top=args.top)
    else:
        topics = crawler.fetch_hot_topics(sources=[args.source], top_per_source=args.top)

    print(f'\n=== {len(topics)} 条热点 ===')
    for t in topics[:args.top]:
        heat_str = f'heat={t["heat"]}' if isinstance(t['heat'], (int, float)) else ''
        print(f'  [{t["source"]}#{t["rank"]}] {t["title"][:50]} {heat_str}')

    if args.output:
        crawler.save_topics(topics, args.output)
