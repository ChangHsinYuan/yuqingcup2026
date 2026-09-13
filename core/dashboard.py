#!/usr/bin/env python3
"""仪表盘生成器 — HTML 可视化任务队列状态

生成自包含 HTML 文件，展示：
  - 队列统计（queued/running/completed/failed）
  - 任务列表（ID/概念/状态/进度/时间）
  - 已完成任务的缩略图 + 视频链接
  - 选题批次记录
  - 自动刷新（meta refresh 30s）
"""
import json
import os
import sys
import html
import base64
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def _status_badge(status):
    colors = {
        'queued': '#6b7280',
        'running': '#3b82f6',
        'completed': '#22c55e',
        'failed': '#ef4444',
        'cancelled': '#9ca3af',
    }
    color = colors.get(status, '#6b7280')
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{status.upper()}</span>'


def _format_duration(created, completed):
    if not created or not completed:
        return '-'
    try:
        c = datetime.fromisoformat(created)
        d = datetime.fromisoformat(completed)
        delta = (d - c).total_seconds()
        if delta < 60:
            return f'{delta:.0f}s'
        m, s = divmod(int(delta), 60)
        h, m = divmod(m, 60)
        if h:
            return f'{h}h{m}m{s}s'
        return f'{m}m{s}s'
    except Exception:
        return '-'


def _get_thumbnail(output_dir, task_id):
    """尝试找到任务的缩略图（首帧抽帧或 character ref 图）"""
    task_dir = os.path.join(output_dir, task_id)

    # 优先找 character ref 图
    ref_patterns = [
        os.path.join(task_dir, 'character', 'ref.png'),
        os.path.join(task_dir, 'character', 'ref.jpg'),
        os.path.join(task_dir, 'character', 'ref_*.png'),
    ]
    for p in ref_patterns:
        matches = glob.glob(p)
        if matches:
            return matches[0]

    # 找 clips 首帧
    clip_patterns = [
        os.path.join(task_dir, 'clips', 'shot_1_frame_*.jpg'),
        os.path.join(task_dir, 'clips', '*_frame_1.jpg'),
    ]
    for p in clip_patterns:
        matches = glob.glob(p)
        if matches:
            return sorted(matches)[0]

    # 找 meta.json 中的 preview
    meta_path = os.path.join(task_dir, 'meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            clips = meta.get('clips', [])
            if clips:
                preview = clips[0].get('preview_frame', '')
                if preview and os.path.exists(preview):
                    return preview
        except Exception:
            pass

    return None


def _embed_image(path, max_size='160px'):
    """将图片 base64 嵌入 HTML"""
    if not path or not os.path.exists(path):
        return '<div style="width:160px;height:90px;background:#1a1a2e;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#555;font-size:11px;">No preview</div>'
    try:
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(path)[1].lower()
        mime = 'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png'
        return f'<img src="data:{mime};base64,{data}" style="width:{max_size};height:auto;border-radius:4px;max-height:90px;object-fit:cover;" />'
    except Exception:
        return '<div style="width:160px;height:90px;background:#1a1a2e;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#555;font-size:11px;">Error</div>'


def generate_dashboard_html(queue):
    """生成仪表盘 HTML（返回字符串，供 FastAPI 直接返回）"""
    config = load_config()
    output_dir = config['output_dir']

    tasks = queue.list_tasks(limit=100)

    # 统计
    stats = {s: queue.count_by_status(s) for s in ('queued', 'running', 'completed', 'failed', 'cancelled')}
    total = sum(stats.values())

    # 任务行
    task_rows = []
    for t in tasks:
        thumb = _get_thumbnail(output_dir, t['task_id']) if t['status'] == 'completed' else None
        thumb_html = _embed_image(thumb) if thumb else '<div style="width:160px;height:90px;background:#1a1a2e;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#555;font-size:11px;">—</div>'

        output_link = ''
        if t.get('output') and os.path.exists(t['output']):
            output_link = f'<a href="file://{t["output"]}" style="color:#3b82f6;">final.mp4</a>'
        elif t.get('output'):
            output_link = f'<span style="color:#555;">{os.path.basename(t["output"])}</span>'

        error_text = ''
        if t.get('error'):
            error_text = f'<div style="color:#ef4444;font-size:11px;margin-top:2px;">{html.escape(t["error"][:80])}</div>'

        opts_html = ''
        if t.get('options'):
            opts = t['options']
            opt_parts = []
            if opts.get('character'):
                opt_parts.append(f'char: {html.escape(str(opts["character"])[:20])}')
            if opts.get('character_mode'):
                opt_parts.append(f'mode: {opts["character_mode"]}')
            if opts.get('duration'):
                opt_parts.append(f'dur: {opts["duration"]}s')
            opts_html = ' · '.join(opt_parts)

        task_rows.append(f'''
        <tr>
            <td style="padding:8px 4px;">{thumb_html}</td>
            <td style="padding:8px;">
                <div style="font-weight:bold;color:#e0e0e0;">{html.escape(t["concept"][:50])}</div>
                <div style="color:#888;font-size:11px;margin-top:2px;">{opts_html}</div>
                {error_text}
            </td>
            <td style="padding:8px;">{_status_badge(t["status"])}</td>
            <td style="padding:8px;color:#aaa;font-size:12px;font-family:monospace;">{t["task_id"]}</td>
            <td style="padding:8px;color:#aaa;font-size:12px;">{t.get("created_at","")}</td>
            <td style="padding:8px;color:#aaa;font-size:12px;">{_format_duration(t.get("created_at"), t.get("completed_at"))}</td>
            <td style="padding:8px;">{output_link}</td>
        </tr>''')

    # Scout 批次
    scout_html = ''
    try:
        with queue._get_conn() as conn:
            rows = conn.execute(
                'SELECT batch_id, scraped_at, candidates_json FROM scout_batches ORDER BY scraped_at DESC LIMIT 5'
            ).fetchall()
        if rows:
            scout_items = []
            for r in rows:
                candidates = json.loads(r[2]) if r[2] else []
                cand_items = []
                for c in candidates[:5]:
                    score = c.get('predicted_score', '?')
                    concept = html.escape(c.get('concept', '')[:40])
                    cand_items.append(f'<li><span style="color:#fbbf24;font-weight:bold;">[{score}]</span> {concept}</li>')
                scout_items.append(f'''
                <div style="background:#1a1a2e;border-radius:6px;padding:12px;margin-bottom:8px;">
                    <div style="color:#888;font-size:11px;margin-bottom:4px;">Batch: {r[0]} · {r[1]}</div>
                    <ul style="margin:0;padding-left:16px;color:#ccc;font-size:13px;">{" ".join(cand_items)}</ul>
                </div>''')
            scout_html = '<br>'.join(scout_items)
    except Exception:
        pass

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="30">
    <title>Vidance Dashboard</title>
    <style>
        body {{ background: #0f0f23; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; }}
        h1 {{ color: #fff; font-size: 24px; margin: 0 0 20px 0; }}
        h2 {{ color: #aaa; font-size: 16px; margin: 24px 0 12px 0; border-bottom: 1px solid #333; padding-bottom: 6px; }}
        .stats {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
        .stat-card {{ background: #1a1a2e; border-radius: 8px; padding: 16px 24px; min-width: 120px; text-align: center; }}
        .stat-num {{ font-size: 32px; font-weight: bold; }}
        .stat-label {{ color: #888; font-size: 12px; margin-top: 4px; text-transform: uppercase; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ text-align: left; padding: 8px 4px; color: #888; font-size: 12px; text-transform: uppercase; border-bottom: 1px solid #333; }}
        td {{ border-bottom: 1px solid #222; vertical-align: top; }}
        .header-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
        .timestamp {{ color: #555; font-size: 12px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header-bar">
            <h1>Vidance Task Dashboard</h1>
            <div class="timestamp">Last updated: {now_str} · Auto-refresh 30s</div>
        </div>

        <div class="stats">
            <div class="stat-card"><div class="stat-num" style="color:#6b7280;">{stats["queued"]}</div><div class="stat-label">Queued</div></div>
            <div class="stat-card"><div class="stat-num" style="color:#3b82f6;">{stats["running"]}</div><div class="stat-label">Running</div></div>
            <div class="stat-card"><div class="stat-num" style="color:#22c55e;">{stats["completed"]}</div><div class="stat-label">Completed</div></div>
            <div class="stat-card"><div class="stat-num" style="color:#ef4444;">{stats["failed"]}</div><div class="stat-label">Failed</div></div>
            <div class="stat-card"><div class="stat-num" style="color:#9ca3af;">{stats["cancelled"]}</div><div class="stat-label">Cancelled</div></div>
            <div class="stat-card"><div class="stat-num" style="color:#fbbf24;">{total}</div><div class="stat-label">Total</div></div>
        </div>

        <h2>Task Queue ({len(tasks)} tasks)</h2>
        <table>
            <thead>
                <tr>
                    <th>Preview</th>
                    <th>Concept</th>
                    <th>Status</th>
                    <th>Task ID</th>
                    <th>Created</th>
                    <th>Duration</th>
                    <th>Output</th>
                </tr>
            </thead>
            <tbody>
                {''.join(task_rows) if task_rows else '<tr><td colspan="7" style="text-align:center;padding:40px;color:#555;">No tasks yet</td></tr>'}
            </tbody>
        </table>

        <h2>Scout Batches</h2>
        {scout_html if scout_html else '<div style="color:#555;padding:20px;">No scout batches yet</div>'}
    </div>
</body>
</html>'''


def generate_dashboard(queue, output_path):
    """生成仪表盘 HTML 文件"""
    html_content = generate_dashboard_html(queue)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f'  [dashboard] saved: {output_path}')
    return output_path


if __name__ == '__main__':
    from core.scheduler import TaskQueue
    q = TaskQueue()
    out = os.path.join(os.path.dirname(__file__), '..', 'output', 'dashboard.html')
    generate_dashboard(q, out)
