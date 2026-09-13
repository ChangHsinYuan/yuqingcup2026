#!/usr/bin/env python3
"""FastAPI 异步任务队列服务 — HTTP API 提交/查询/管理视频生成任务

启动:
  python core/api_server.py                     # 默认 0.0.0.0:8894
  python core/api_server.py --port 9000         # 自定义端口

API:
  POST   /api/tasks          提交任务
  GET    /api/tasks          列出任务 (?status=queued&limit=20)
  GET    /api/tasks/{id}     查询任务详情
  DELETE /api/tasks/{id}     取消任务
  GET    /api/dashboard      HTML 仪表盘
  GET    /api/health         健康检查
  POST   /api/scout          选题（爬热点→概念候选）
  GET    /api/scout/{id}     查询选题结果
  POST   /api/clone_voice    音色克隆（爬人声→CosyVoice，异步）
  GET    /api/clone_voice/{id}  查询克隆任务
  GET    /api/voices         已注册自定义音色
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from core.scheduler import TaskQueue, Scheduler, load_config

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')

app = FastAPI(title='Vidance Task Queue', version='4.2.0')
queue = TaskQueue()
scheduler = None


class TaskSubmit(BaseModel):
    concept: str
    character: Optional[str] = None
    character_mode: str = 'flux'
    voice: Optional[str] = None
    duration: Optional[float] = None
    slowmo: Optional[int] = None
    lut: Optional[str] = None
    bgm: Optional[str] = None
    no_rife: bool = False
    no_color: bool = False
    no_bgm: bool = False


class ScoutRequest(BaseModel):
    top_per_source: int = 15
    account_type: str = '影视解说'
    n_candidates: int = 5


class CloneVoiceRequest(BaseModel):
    keyword: str = '新闻播报'
    name: str = ''
    index: int = 0
    desc: Optional[str] = None
    reload_tts: bool = True


@app.on_event('startup')
def startup():
    global scheduler
    scheduler = Scheduler(queue, max_concurrent=1)
    t = threading.Thread(target=scheduler.run, kwargs={'poll_interval': 5}, daemon=True)
    t.start()
    print(f'  [api] scheduler started (max_concurrent=1)')


@app.get('/api/health')
def health():
    return {
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'queue': {
            'queued': queue.count_by_status('queued'),
            'running': queue.count_by_status('running'),
            'completed': queue.count_by_status('completed'),
            'failed': queue.count_by_status('failed'),
        },
    }


@app.post('/api/tasks')
def submit_task(req: TaskSubmit):
    opts = {
        k: v for k, v in req.model_dump().items()
        if k != 'concept' and v is not None and v is not False
    }
    task_id = queue.submit(req.concept, options=opts)
    return {'task_id': task_id, 'status': 'queued', 'concept': req.concept}


@app.get('/api/tasks')
def list_tasks(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    tasks = queue.list_tasks(status=status, limit=limit)
    return {'tasks': tasks, 'count': len(tasks)}


@app.get('/api/tasks/{task_id}')
def get_task(task_id: str):
    task = queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    return task


@app.delete('/api/tasks/{task_id}')
def cancel_task(task_id: str):
    ok, msg = queue.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {'task_id': task_id, 'status': 'cancelled'}


@app.post('/api/scout')
def scout(req: ScoutRequest):
    from utils.crawler import Crawler
    from utils.llm import LLMClient

    config = load_config()
    crawler = Crawler()
    llm = LLMClient(config)

    topics = crawler.fetch_hot_topics(top_per_source=req.top_per_source)
    candidates = llm.scout_topics(
        topics, account_type=req.account_type, n_candidates=req.n_candidates)

    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    queue.save_scout_batch(batch_id, topics, candidates)
    return {
        'batch_id': batch_id,
        'topics_count': len(topics),
        'candidates': candidates,
    }


@app.get('/api/scout/{batch_id}')
def get_scout(batch_id: str):
    batch = queue.get_scout_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail='Scout batch not found')
    return batch


@app.get('/api/dashboard', response_class=HTMLResponse)
def dashboard():
    from core.dashboard import generate_dashboard_html
    return generate_dashboard_html(queue)


# ── v4 M3: 音色克隆（异步线程，下载+VAD+转写约 1-3 分钟） ──
_voice_jobs = {}
_voice_jobs_lock = threading.Lock()


def _run_clone_voice(job_id: str, req: CloneVoiceRequest):
    from utils.voice_clone import VoiceCloner
    try:
        vc = VoiceCloner()
        name = req.name or f'clone_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        report = vc.clone(req.keyword, name, index=req.index, desc=req.desc)

        if req.reload_tts:
            try:
                import urllib.request
                urllib.request.urlopen(
                    urllib.request.Request('http://127.0.0.1:9880/voices/reload',
                                           method='POST'), timeout=10)
            except Exception:
                pass

        with _voice_jobs_lock:
            _voice_jobs[job_id].update({
                'status': 'completed', 'voice': f'cosy-{name}',
                'report': {k: v for k, v in report.items() if k != 'steps'},
                'sample': report.get('steps', {}).get('test', {}).get('sample'),
                'completed_at': datetime.now().isoformat(),
            })
    except Exception as e:
        with _voice_jobs_lock:
            _voice_jobs[job_id].update({
                'status': 'failed', 'error': str(e),
                'completed_at': datetime.now().isoformat(),
            })


@app.post('/api/clone_voice')
def clone_voice(req: CloneVoiceRequest):
    job_id = f'voice_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    with _voice_jobs_lock:
        _voice_jobs[job_id] = {
            'job_id': job_id, 'keyword': req.keyword, 'name': req.name,
            'status': 'running', 'created_at': datetime.now().isoformat(),
        }
    threading.Thread(target=_run_clone_voice, args=(job_id, req), daemon=True).start()
    return {'job_id': job_id, 'status': 'running'}


@app.get('/api/clone_voice/{job_id}')
def get_clone_voice(job_id: str):
    with _voice_jobs_lock:
        job = _voice_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return job


@app.get('/api/voices')
def list_voices():
    from utils.voice_clone import VoiceCloner
    return {'custom': VoiceCloner().list_voices()}


if __name__ == '__main__':
    import uvicorn

    parser = argparse.ArgumentParser(description='Vidance API Server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8894)
    args = parser.parse_args()

    print(f'  [api] starting on {args.host}:{args.port}')
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')
