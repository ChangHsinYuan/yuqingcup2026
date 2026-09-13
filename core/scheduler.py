#!/usr/bin/env python3
"""异步任务队列 — SQLite 持久化 + 并发调度

用法:
  from core.scheduler import TaskQueue, Scheduler
  
  # 提交任务
  queue = TaskQueue()
  task_id = queue.submit(concept="一只猫在月球上跳舞", options={...})
  
  # 查询
  task = queue.get_task(task_id)
  tasks = queue.list_tasks(status="running")
  
  # 调度器（后台进程）
  scheduler = Scheduler(queue, max_concurrent=2)
  scheduler.run()  # 阻塞，轮询队列

CLI:
  # 启动调度器
  python core/scheduler.py --max-concurrent 2
  
  # 提交任务
  python core/scheduler.py submit "一只猫在月球上跳舞" --character "红色小狐狸"
  
  # 查看队列
  python core/scheduler.py list
  python core/scheduler.py status <task_id>
"""
import json
import os
import sys
import time
import sqlite3
import threading
import subprocess
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'output', 'tasks.sqlite')

VALID_STATUSES = ('queued', 'running', 'completed', 'failed', 'cancelled')
VALID_STAGES = ('queued', 'generating', 'post_processing', 'completed', 'failed')


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


class TaskQueue:
    """SQLite 持久化任务队列"""

    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._lock = threading.Lock()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    concept TEXT NOT NULL,
                    status TEXT DEFAULT 'queued',
                    stage TEXT DEFAULT 'queued',
                    progress REAL DEFAULT 0,
                    options TEXT DEFAULT '{}',
                    output TEXT,
                    error TEXT,
                    created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    pid INTEGER
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scout_batches (
                    batch_id TEXT PRIMARY KEY,
                    scraped_at TEXT,
                    topics_json TEXT,
                    candidates_json TEXT,
                    selected_json TEXT
                )
            ''')

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def submit(self, concept, options=None, task_id=None):
        """提交新任务到队列"""
        if task_id is None:
            task_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + str(int(time.time() * 1000) % 10000)
        opts = json.dumps(options or {}, ensure_ascii=False)
        now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

        with self._lock, self._get_conn() as conn:
            conn.execute(
                'INSERT INTO tasks (task_id, concept, status, stage, options, created_at) '
                'VALUES (?, ?, "queued", "queued", ?, ?)',
                (task_id, concept, opts, now),
            )
            conn.commit()
        print(f'  [queue] submitted: {task_id} — {concept[:40]}')
        return task_id

    def get_task(self, task_id):
        """查询单个任务"""
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT * FROM tasks WHERE task_id = ?', (task_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_tasks(self, status=None, limit=50):
        """列出任务"""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    'SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?', (limit,)
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_task(self, task_id, **fields):
        """更新任务字段"""
        valid = {'status', 'stage', 'progress', 'output', 'error',
                 'started_at', 'completed_at', 'pid'}
        updates = {k: v for k, v in fields.items() if k in valid}
        if not updates:
            return

        set_clause = ', '.join(f'{k} = ?' for k in updates)
        values = list(updates.values()) + [task_id]

        with self._lock, self._get_conn() as conn:
            conn.execute(
                f'UPDATE tasks SET {set_clause} WHERE task_id = ?', values
            )
            conn.commit()

    def cancel_task(self, task_id):
        """取消任务（仅 queued 状态可取消）"""
        task = self.get_task(task_id)
        if not task:
            return False, 'task not found'
        if task['status'] != 'queued':
            return False, f'cannot cancel task in {task["status"]} state'
        self.update_task(task_id, status='cancelled')
        return True, 'cancelled'

    def get_next_queued(self):
        """获取下一个排队任务（FIFO）"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' "
                "ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count_by_status(self, status):
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT COUNT(*) FROM tasks WHERE status = ?', (status,)
            ).fetchone()
        return row[0] if row else 0

    @staticmethod
    def _row_to_dict(row):
        if not row:
            return None
        cols = ['task_id', 'concept', 'status', 'stage', 'progress', 'options',
                'output', 'error', 'created_at', 'started_at', 'completed_at', 'pid']
        d = dict(zip(cols, row))
        try:
            d['options'] = json.loads(d.get('options') or '{}')
        except (json.JSONDecodeError, TypeError):
            d['options'] = {}
        return d

    def save_scout_batch(self, batch_id, topics, candidates, selected=None):
        """保存选题批次记录"""
        now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        with self._get_conn() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO scout_batches '
                '(batch_id, scraped_at, topics_json, candidates_json, selected_json) '
                'VALUES (?, ?, ?, ?, ?)',
                (batch_id, now,
                 json.dumps(topics, ensure_ascii=False),
                 json.dumps(candidates, ensure_ascii=False),
                 json.dumps(selected or [], ensure_ascii=False)),
            )
            conn.commit()

    def get_scout_batch(self, batch_id):
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT * FROM scout_batches WHERE batch_id = ?', (batch_id,)
            ).fetchone()
        if not row:
            return None
        return {
            'batch_id': row[0],
            'scraped_at': row[1],
            'topics': json.loads(row[2]),
            'candidates': json.loads(row[3]),
            'selected': json.loads(row[4]),
        }


class Scheduler:
    """并发任务调度器"""

    def __init__(self, queue=None, max_concurrent=2, config=None):
        self.queue = queue or TaskQueue()
        self.max_concurrent = max_concurrent
        self.config = config or load_config()
        self._running_threads = {}

    def run(self, poll_interval=5):
        """主调度循环（阻塞）"""
        print(f'  [scheduler] started, max_concurrent={self.max_concurrent}')
        while True:
            self._cleanup_finished()
            running = self.queue.count_by_status('running')

            while running < self.max_concurrent:
                task = self.queue.get_next_queued()
                if not task:
                    break
                self._start_task(task)
                running += 1

            time.sleep(poll_interval)

    def _cleanup_finished(self):
        """清理已完成的线程"""
        finished = [tid for tid, t in self._running_threads.items() if not t.is_alive()]
        for tid in finished:
            del self._running_threads[tid]

    def _start_task(self, task):
        """启动一个任务线程"""
        now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.queue.update_task(task['task_id'], status='running', stage='generating',
                               started_at=now)

        t = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        self._running_threads[task['task_id']] = t
        t.start()
        print(f'  [scheduler] started: {task["task_id"]} — {task["concept"][:40]}')

    def _run_task(self, task):
        """执行单个任务（子进程调用 vidance.py auto）"""
        task_id = task['task_id']
        opts = task['options']
        concept = task['concept']

        cmd = [sys.executable, '-u', os.path.join(os.path.dirname(__file__), 'vidance.py'), 'auto', concept]

        if opts.get('character'):
            cmd += ['--character', opts['character']]
        if opts.get('character_mode'):
            cmd += ['--character-mode', opts['character_mode']]
        if opts.get('voice'):
            cmd += ['--voice', opts['voice']]
        if opts.get('duration'):
            cmd += ['--duration', str(opts['duration'])]
        if opts.get('slowmo'):
            cmd += ['--slowmo', str(opts['slowmo'])]
        if opts.get('lut'):
            cmd += ['--lut', opts['lut']]
        if opts.get('bgm'):
            cmd += ['--bgm', opts['bgm']]
        if opts.get('no_rife'):
            cmd += ['--no-rife']
        if opts.get('no_color'):
            cmd += ['--no-color']
        if opts.get('no_bgm'):
            cmd += ['--no-bgm']

        cmd += ['--task-id', task_id]

        log_path = os.path.join(self.config['output_dir'], task_id, 'scheduler.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        try:
            with open(log_path, 'w') as logf:
                proc = subprocess.run(
                    cmd, stdout=logf, stderr=subprocess.STDOUT,
                    timeout=7200,
                )

            if proc.returncode == 0:
                final_path = os.path.join(self.config['output_dir'], task_id, 'final.mp4')
                now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                self.queue.update_task(
                    task_id, status='completed', stage='completed',
                    progress=1.0, output=final_path, completed_at=now,
                )
                print(f'  [scheduler] completed: {task_id}')
            else:
                now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                self.queue.update_task(
                    task_id, status='failed', stage='failed',
                    error=f'exit code {proc.returncode}', completed_at=now,
                )
                print(f'  [scheduler] failed: {task_id} (exit {proc.returncode})')

        except subprocess.TimeoutExpired:
            now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            self.queue.update_task(
                task_id, status='failed', stage='failed',
                error='timeout (7200s)', completed_at=now,
            )
            print(f'  [scheduler] timeout: {task_id}')
        except Exception as e:
            now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            self.queue.update_task(
                task_id, status='failed', stage='failed',
                error=str(e), completed_at=now,
            )
            print(f'  [scheduler] error: {task_id} — {e}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vidance 任务队列 + 调度器')
    sub = parser.add_subparsers(dest='command')

    # 启动调度器
    run_p = sub.add_parser('run', help='启动调度器')
    run_p.add_argument('--max-concurrent', '-c', type=int, default=2)
    run_p.add_argument('--poll', type=float, default=5)

    # 提交任务
    submit_p = sub.add_parser('submit', help='提交任务')
    submit_p.add_argument('concept', help='视频概念')
    submit_p.add_argument('--character', '-ch', default=None)
    submit_p.add_argument('--character-mode', '-cm', default='flux')
    submit_p.add_argument('--voice', '-v', default=None)
    submit_p.add_argument('--duration', '-d', type=float, default=None)
    submit_p.add_argument('--no-rife', action='store_true')
    submit_p.add_argument('--no-color', action='store_true')
    submit_p.add_argument('--no-bgm', action='store_true')

    # 列表
    list_p = sub.add_parser('list', help='列出任务')
    list_p.add_argument('--status', '-s', default=None)
    list_p.add_argument('--limit', '-n', type=int, default=20)

    # 状态
    status_p = sub.add_parser('status', help='查看任务状态')
    status_p.add_argument('task_id')

    # 取消
    cancel_p = sub.add_parser('cancel', help='取消任务')
    cancel_p.add_argument('task_id')

    # 仪表盘
    dash_p = sub.add_parser('dashboard', help='生成仪表盘 HTML')
    dash_p.add_argument('-o', '--output', default=None)

    args = parser.parse_args()

    queue = TaskQueue()

    if args.command == 'run':
        scheduler = Scheduler(queue, max_concurrent=args.max_concurrent)
        scheduler.run(poll_interval=args.poll)

    elif args.command == 'submit':
        opts = {}
        if args.character:
            opts['character'] = args.character
        if args.character_mode:
            opts['character_mode'] = args.character_mode
        if args.voice:
            opts['voice'] = args.voice
        if args.duration:
            opts['duration'] = args.duration
        if args.no_rife:
            opts['no_rife'] = True
        if args.no_color:
            opts['no_color'] = True
        if args.no_bgm:
            opts['no_bgm'] = True
        task_id = queue.submit(args.concept, options=opts)
        print(f'Task ID: {task_id}')

    elif args.command == 'list':
        tasks = queue.list_tasks(status=args.status, limit=args.limit)
        print(f'{"Task ID":<25} {"Status":<12} {"Concept":<40} {"Created"}')
        print('-' * 100)
        for t in tasks:
            print(f'{t["task_id"]:<25} {t["status"]:<12} {t["concept"][:40]:<40} {t.get("created_at","")}')

    elif args.command == 'status':
        t = queue.get_task(args.task_id)
        if not t:
            print('Task not found')
            sys.exit(1)
        print(json.dumps(t, ensure_ascii=False, indent=2))

    elif args.command == 'cancel':
        ok, msg = queue.cancel_task(args.task_id)
        print(f'{msg}')

    elif args.command == 'dashboard':
        from core.dashboard import generate_dashboard
        out = args.output or os.path.join(
            os.path.dirname(__file__), '..', 'output', 'dashboard.html')
        generate_dashboard(queue, out)
        print(f'Dashboard: {out}')

    else:
        parser.print_help()
