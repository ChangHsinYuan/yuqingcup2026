#!/usr/bin/env python3
"""v6 对话状态机 — prompt 多轮核实（M2）

概念信息不全时，多轮对话向用户核实缺失维度，攒够/达轮次上限后输出增强概念，
供 auto/编剧流水线使用。状态机 + SQLite 持久化（复用 output/tasks.sqlite）。

用法:
  from utils.dialog import DialogManager
  dm = DialogManager()
  s = dm.start('深海探险')          # → {dialog_id, status, missing, questions, concept}
  s = dm.reply(dialog_id, '一只发光蓝鲸，深海幽暗场景')  # → 更新
  dm.locked_concept(dialog_id)      # 返回增强概念
"""
import os
import sqlite3
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.llm import LLMClient

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'output', 'tasks.sqlite')
MAX_ROUNDS = 3


class DialogManager:
    """多轮需求核实状态机，SQLite 持久化。"""

    def __init__(self, db_path=None, llm=None):
        self.db_path = db_path or DEFAULT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.llm = llm or LLMClient()
        self._init_db()

    def _init_db(self):
        with self._conn() as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS dialogs (
                    id TEXT PRIMARY KEY,
                    concept TEXT,
                    turns TEXT,        -- JSON [{q, a}]
                    dimensions TEXT,   -- JSON {subject:1,...}
                    missing TEXT,      -- JSON [..]
                    status TEXT,       -- OPEN|COLLECTING|COMPLETE|LOCKED
                    created_at TEXT,
                    updated_at TEXT
                )''')

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ══ 读取 ══
    def get(self, dialog_id):
        with self._conn() as c:
            row = c.execute('SELECT * FROM dialogs WHERE id=?', (dialog_id,)).fetchone()
        if not row:
            return None
        return {
            'id': row['id'], 'concept': row['concept'],
            'turns': json.loads(row['turns'] or '[]'),
            'dimensions': json.loads(row['dimensions'] or '{}'),
            'missing': json.loads(row['missing'] or '[]'),
            'status': row['status'],
            'created_at': row['created_at'], 'updated_at': row['updated_at'],
        }

    # ══ 启动 ══
    def start(self, concept):
        dialog_id = f'dlg_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        assess = self.llm.assess_completeness(concept)
        status = 'COMPLETE' if assess['complete'] else 'OPEN'
        with self._conn() as c:
            c.execute(
                'INSERT INTO dialogs (id,concept,turns,dimensions,missing,status,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (dialog_id, concept, json.dumps([]),
                 json.dumps(assess['dimensions']), json.dumps(assess['missing']),
                 status, datetime.now().isoformat(), datetime.now().isoformat()))
        return {'dialog_id': dialog_id, 'status': status,
                'dimensions': assess['dimensions'], 'missing': assess['missing'],
                'questions': assess['questions'], 'concept': concept}

    # ══ 回复 ══
    def reply(self, dialog_id, text):
        rec = self.get(dialog_id)
        if not rec:
            return {'error': 'dialog not found'}
        turns = rec['turns'] + [{'a': text}]
        rounds = len(turns)
        # 组装已确认上下文：原概念 + 各轮回答 → 重新评估
        combined = rec['concept']
        for t in turns:
            combined += '；' + t.get('a', '')
        assess = self.llm.assess_completeness(combined)
        # 记忆：拿新的 dimensions/missing，补到已有（不覆盖已确认）
        dims = dict(rec['dimensions'])
        dims.update(assess['dimensions'])
        missing = assess['missing']
        # 与 assess_completeness 同口径：全 6 维齐才 COMPLETE（prompt 靠多轮追问变长变详细），
        # 3 轮上限 LOCKED 强制放行
        status = ('COMPLETE' if all(dims.get(d, 0) for d in
                                     ('subject', 'scene', 'emotion', 'style', 'duration', 'shot'))
                  else ('COLLECTING' if rounds > 1 else 'OPEN'))
        if rounds >= MAX_ROUNDS:
            status = 'LOCKED'  # 轮次上限，强制放行
        with self._conn() as c:
            c.execute(
                'UPDATE dialogs SET turns=?,dimensions=?,missing=?,status=?,updated_at=? WHERE id=?',
                (json.dumps(turns), json.dumps(dims), json.dumps(missing),
                 status, datetime.now().isoformat(), dialog_id))
        out = {'dialog_id': dialog_id, 'status': status, 'turns': turns,
               'dimensions': dims, 'missing': missing,
               'questions': assess['questions'],
               'concept': self.locked_concept(dialog_id)}
        return out

    # ══ 增强概念 ══
    def locked_concept(self, dialog_id, default_duration=15):
        """把对话中的补充信息合并进原概念，得到可喂给编剧/流水线的增强概念。"""
        rec = self.get(dialog_id)
        if not rec:
            return ''
        text = rec['concept']
        for t in rec['turns']:
            a = (t.get('a') or '').strip()
            if a and a not in ('随便', '都可以', '随便吧'):
                text += f'；{a}'
        return text

    # ══ 列出已存对话 ══
    def list(self, limit=20):
        with self._conn() as c:
            rows = c.execute(
                'SELECT id,concept,status,created_at FROM dialogs ORDER BY created_at DESC LIMIT ?',
                (limit,)).fetchall()
        return [dict(r) for r in rows]


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(prog='dialog', description='v6 对话状态机')
    p.add_argument('--start', help='概念，启动新对话')
    args = p.parse_args()
    dm = DialogManager()
    s = dm.start(args.start)
    print(json.dumps(s, ensure_ascii=False, indent=2))
