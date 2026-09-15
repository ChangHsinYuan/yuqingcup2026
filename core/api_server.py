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
  POST   /api/clip           FunClip 长素材语义裁剪（异步）
  GET    /api/clip/{id}      查询裁剪任务
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
    mode: str = 'auto'  # v7: auto|fast|hot（scheduler 分发 vidance.py 子命令）
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
    # v7 fast/hot 参数
    images_source: Optional[str] = None
    images_count: Optional[int] = None
    effects: Optional[str] = None
    motion: Optional[str] = None
    stt: bool = False
    top_each: Optional[int] = None
    account: Optional[str] = None


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


class ClipRequest(BaseModel):
    input: str
    instructions: str
    output: Optional[str] = None
    use_llm: bool = True


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


# ── v4 M4: FunClip 长素材语义裁剪（异步线程，ASR+LLM 约 30s-2min） ──
_clip_jobs = {}
_clip_jobs_lock = threading.Lock()


def _run_clip(job_id: str, req: ClipRequest):
    from utils.funclip import FunClip
    try:
        out = req.output
        if out and not os.path.isabs(out):
            out = os.path.join(load_config().get('output_dir', 'output'), out)
        if not out:
            base, ext = os.path.splitext(req.input)
            out = base + '_clipped' + (ext or '.wav')

        llm = None
        if req.use_llm:
            from utils.llm import LLMClient
            llm = LLMClient()

        fc = FunClip()
        r = fc.smart_clip(req.input, req.instructions, out, llm=llm)
        with _clip_jobs_lock:
            _clip_jobs[job_id].update({
                'status': 'completed', 'output': out,
                'segments': r['segments'], 'keep': r['keep'],
                'sentences': r['sentences'],
                'completed_at': datetime.now().isoformat(),
            })
    except Exception as e:
        with _clip_jobs_lock:
            _clip_jobs[job_id].update({
                'status': 'failed', 'error': str(e),
                'completed_at': datetime.now().isoformat(),
            })


@app.post('/api/clip')
def clip(req: ClipRequest):
    job_id = f'clip_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    with _clip_jobs_lock:
        _clip_jobs[job_id] = {
            'job_id': job_id, 'input': req.input,
            'instructions': req.instructions,
            'status': 'running', 'created_at': datetime.now().isoformat(),
        }
    threading.Thread(target=_run_clip, args=(job_id, req), daemon=True).start()
    return {'job_id': job_id, 'status': 'running'}


@app.get('/api/clip/{job_id}')
def get_clip(job_id: str):
    with _clip_jobs_lock:
        job = _clip_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return job


# ══ v6 prompt 多轮核实 dialog 端点 ══

class DialogStart(BaseModel):
    concept: str


class DialogReply(BaseModel):
    text: str


@app.post('/api/dialog/start')
def dialog_start(req: DialogStart):
    """启动多轮需求核实会话 → {dialog_id, status, missing, questions, concept}"""
    from utils.dialog import DialogManager
    dm = DialogManager()
    s = dm.start(req.concept)
    return {'dialog_id': s['dialog_id'], 'status': s['status'],
            'dimensions': s['dimensions'], 'missing': s['missing'],
            'questions': s['questions'], 'concept': s['concept']}


@app.get('/api/dialog/{dialog_id}')
def dialog_get(dialog_id: str):
    from utils.dialog import DialogManager
    dm = DialogManager()
    s = dm.get(dialog_id)
    if not s:
        raise HTTPException(status_code=404, detail='dialog not found')
    return s


@app.post('/api/dialog/{dialog_id}/reply')
def dialog_reply(dialog_id: str, req: DialogReply):
    from utils.dialog import DialogManager
    dm = DialogManager()
    s = dm.reply(dialog_id, req.text)
    if 'error' in s:
        raise HTTPException(status_code=404, detail=s['error'])
    return s


# ══ v7 前端：只读端点 + / 斜杠命令统一分发 + 静态 UI ══

# ── /api/video/{task_id}：成片视频流 ──
@app.get('/api/video/{task_id}')
def video_stream(task_id: str):
    task = queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    output = task.get('output')
    if not output or not os.path.exists(output):
        # 兜底：output/{task_id}/final.mp4 或 final_slow.mp4
        for name in ('final.mp4', 'final_slow.mp4'):
            cand = os.path.join(load_config().get('output_dir', 'output'), task_id, name)
            if os.path.exists(cand):
                output = cand
                break
    if not output or not os.path.exists(output):
        raise HTTPException(status_code=404, detail='Video not found (task may still be running)')
    return FileResponse(output, media_type='video/mp4', filename=os.path.basename(output))


# ── /api/options：枚举（音色/LUT/BGM/特效/运镜），供前端下拉 ──
@app.get('/api/options')
def options():
    voices = []
    try:
        from utils.voice_clone import VoiceCloner
        voices = [v.get('name') if isinstance(v, dict) else str(v)
                  for v in VoiceCloner().list_voices()]
    except Exception:
        pass
    return {
        'voices': voices,
        'character_modes': ['auto', 'flux', '3dgs', 'mesh'],
        'luts': ['cinematic', 'warm', 'cool', 'vintage', 'vivid', 'soft'],
        'bgms': ['calm', 'uplifting', 'mysterious', 'dramatic', 'playful', 'epic'],
        'effects': ['auto', 'off'],
        'motions': ['pan', 'zoom-in', 'zoom-out'],
        'images_sources': ['crawl', 'flux'],
        'accounts': ['热门资讯', '影视解说', '萌宠', '情感'],
    }


# ── /api/tools：/ 斜杠命令清单（前端补全渲染） ──
TOOLS = [
    {'name': 'auto', 'help': '概念→LLM编剧→生成→后处理（完整流水线）',
     'params': [
         {'key': 'concept', 'type': 'text', 'required': True, 'desc': '视频概念（中文）'},
         {'key': '--character', 'type': 'text', 'desc': '角色描述'},
         {'key': '--character-mode', 'type': 'choice', 'choices': ['auto', 'flux', '3dgs', 'mesh'], 'desc': '角色锚模式'},
         {'key': '--voice', 'type': 'text', 'desc': 'TTS 音色'},
         {'key': '--duration', 'type': 'number', 'desc': '目标时长（秒）'},
         {'key': '--lut', 'type': 'choice', 'choices': ['cinematic', 'warm', 'cool', 'vintage', 'vivid', 'soft'], 'desc': '调色风格'},
         {'key': '--bgm', 'type': 'choice', 'choices': ['calm', 'uplifting', 'mysterious', 'dramatic', 'playful', 'epic'], 'desc': 'BGM mood'},
         {'key': '--slowmo', 'type': 'number', 'desc': '慢动作倍率'},
     ]},
    {'name': 'fast', 'help': '快速营销号链路：爬图+运镜+TTS（分钟级出片）',
     'params': [
         {'key': 'concept', 'type': 'text', 'required': True, 'desc': '热点概念/旁白主题（中文）'},
         {'key': '--images-source', 'type': 'choice', 'choices': ['crawl', 'flux'], 'desc': '图片来源'},
         {'key': '-n', 'type': 'number', 'desc': '图片/段落数（≤5）'},
         {'key': '--effects', 'type': 'choice', 'choices': ['auto', 'off'], 'desc': 'v5 特效'},
         {'key': '--lut', 'type': 'choice', 'choices': ['cinematic', 'warm', 'cool', 'vintage', 'vivid', 'soft'], 'desc': '调色风格'},
         {'key': '--bgm', 'type': 'choice', 'choices': ['calm', 'uplifting', 'mysterious', 'dramatic', 'playful', 'epic'], 'desc': 'BGM mood'},
         {'key': '--stt', 'type': 'flag', 'desc': 'STT 字幕对齐'},
     ]},
    {'name': 'hot', 'help': '爬实时热点→LLM选题→出片（一步到位）',
     'params': [
         {'key': '-n', 'type': 'number', 'desc': '图片/段落数（≤5）'},
         {'key': '--account', 'type': 'text', 'desc': '账号定位（热门资讯/影视解说/萌宠/情感）'},
         {'key': '--images-source', 'type': 'choice', 'choices': ['crawl', 'flux'], 'desc': '图片来源'},
         {'key': '--effects', 'type': 'choice', 'choices': ['auto', 'off'], 'desc': 'v5 特效'},
     ]},
    {'name': 'ask', 'help': 'v6 多轮核实：补齐概念缺失维度 → 增强概念',
     'params': [
         {'key': 'concept', 'type': 'text', 'required': True, 'desc': '原始概念（中文）'},
     ]},
    {'name': 'clip', 'help': 'FunClip：长素材 ASR 转写+语义裁剪',
     'params': [
         {'key': 'input', 'type': 'text', 'required': True, 'desc': '音频/视频路径'},
         {'key': '-i', 'type': 'text', 'required': True, 'desc': '保留什么（自然语言指令）'},
     ]},
    {'name': 'scout', 'help': '爬热点→概念候选排序',
     'params': [
         {'key': '--account', 'type': 'text', 'desc': '账号定位'},
         {'key': '-n', 'type': 'number', 'desc': '候选数'},
     ]},
    {'name': 'voices', 'help': '列出已注册音色', 'params': []},
    {'name': 'luts', 'help': '列出可用的调色风格（LUT）', 'params': []},
    {'name': 'moods', 'help': '列出可用的 BGM 配乐情绪', 'params': []},
    {'name': 'video', 'help': '播放成片',
     'params': [
         {'key': 'task_id', 'type': 'text', 'required': True, 'desc': '任务 ID'},
     ]},
    {'name': 'tasks', 'help': '列出最近任务',
     'params': [
         {'key': '-n', 'type': 'number', 'desc': '条数（默认 10）'},
         {'key': '--status', 'type': 'text', 'desc': '按状态筛选：queued/running/completed/failed'},
     ]},
    {'name': 'status', 'help': '查询单个任务详情',
     'params': [
         {'key': 'task_id', 'type': 'text', 'required': True, 'desc': '任务 ID'},
     ]},
    {'name': 'trends', 'help': '爬取实时热点（不选题，直接看热榜）',
     'params': [
         {'key': '--top', 'type': 'number', 'desc': '每源条数（默认 10）'},
     ]},
    {'name': 'clean', 'help': '清理旧任务输出（默认保留最近 10 个，--keep 可改）',
     'params': [
         {'key': '--keep', 'type': 'number', 'desc': '保留最近 N 个（默认 10）'},
     ]},
    {'name': 'help', 'help': '列出全部 / 命令', 'params': []},
]


@app.get('/api/tools')
def tools():
    return {'tools': TOOLS}


class ToolRequest(BaseModel):
    args: str = ''


def _parse_tool_args(argstr: str) -> dict:
    """解析命令参数串 → {positional: [...], flags: {key: value}}

    支持：`"概念" --key value --flag` / `概念 --key value`（首 token 或引号内为位置参数）
    """
    import shlex
    try:
        tokens = shlex.split(argstr)
    except ValueError:
        tokens = argstr.split()
    out = {'positional': [], 'flags': {}}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith('--'):
            key = t[2:].replace('-', '_')
            if i + 1 < len(tokens) and not tokens[i + 1].startswith('--'):
                out['flags'][key] = tokens[i + 1]
                i += 2
            else:
                out['flags'][key] = True
                i += 1
        elif t.startswith('-') and len(t) == 2:  # 短参数如 -n
            key = t[1:]
            if i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
                out['flags'][key] = tokens[i + 1]
                i += 2
            else:
                out['flags'][key] = True
                i += 1
        else:
            out['positional'].append(t)
            i += 1
    return out


def _task_id_from_response(resp: dict) -> Optional[str]:
    return resp.get('task_id') if isinstance(resp, dict) else None


@app.post('/api/tool/{name}')
def tool_dispatch(name: str, req: ToolRequest):
    """统一 / 命令分发：解析 args → 调对应后端 → {result, task_id?, message}"""
    spec = next((t for t in TOOLS if t['name'] == name), None)
    if not spec:
        raise HTTPException(status_code=404, detail=f'Unknown tool: {name}')
    a = _parse_tool_args(req.args or '')
    pos = a['positional']
    flags = a['flags']

    # ── help ──
    if name == 'help':
        return {'message': '可用命令：' + '、'.join(f'/{t["name"]} {t["help"]}' for t in TOOLS),
                'tools': TOOLS}

    # ── voices ──
    if name == 'voices':
        from utils.voice_clone import VoiceCloner
        vs = VoiceCloner().list_voices()
        return {'message': f'共 {len(vs)} 个自定义音色', 'voices': vs}

    # ── video ──
    if name == 'video':
        if not pos:
            raise HTTPException(status_code=400, detail='用法: /video {task_id}')
        task_id = pos[0]
        task = queue.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail='Task not found')
        return {'message': f'任务 {task_id} 状态 {task["status"]}',
                'task': task, 'video_url': f'/api/video/{task_id}'}

    # ── luts / moods / tasks / status / trends / clean ──
    if name == 'luts':
        cfg = load_config()
        return {'message': '可用 LUT 风格', 'luts': cfg.get('color', {}).get('styles', ['cinematic'])}
    if name == 'moods':
        cfg = load_config()
        return {'message': '可用 BGM 情绪', 'moods': cfg.get('bgm', {}).get('moods', ['calm'])}
    if name == 'tasks':
        st = flags.get('status')
        n = int(flags.get('n', 10))
        rows = queue.list_tasks(status=st, limit=n)
        return {'message': f'共 {len(rows)} 个任务', 'tasks': rows}
    if name == 'status':
        if not pos:
            raise HTTPException(status_code=400, detail='用法: /status {task_id}')
        task = queue.get_task(pos[0])
        if not task:
            raise HTTPException(status_code=404, detail='Task not found')
        return {'message': f'任务 {pos[0]} 状态 {task["status"]}', 'task': task}
    if name == 'trends':
        from utils.crawler import Crawler
        top = int(flags.get('top', 10))
        topics = Crawler().fetch_hot_topics(top_per_source=top)
        return {'message': f'抓取到 {len(topics)} 条热点', 'topics': topics}
    if name == 'clean':
        keep = int(flags.get('keep', 10))
        import glob as _glob
        out_root = os.path.join(os.path.dirname(__file__), '..', 'output')
        dirs = sorted(_glob.glob(os.path.join(out_root, '2*')), key=os.path.getmtime, reverse=True)
        to_del = dirs[keep:]
        removed = []
        for d in to_del:
            try:
                import shutil as _sh
                _sh.rmtree(d)
                removed.append(os.path.basename(d))
            except Exception as e:
                print(f'  ⚠ clean failed {d}: {e}')
        return {'message': f'清理 {len(removed)} 个旧任务，保留 {keep} 个', 'removed': removed}

    # ── ask（v6 dialog）──
    if name == 'ask':
        if not pos:
            raise HTTPException(status_code=400, detail='用法: /ask 概念')
        from utils.dialog import DialogManager
        dm = DialogManager()
        s = dm.start(' '.join(pos))
        return {'message': f'评估 {s["status"]}，缺失 {len(s["missing"])} 项', 'dialog': s}

    # ── scout ──
    if name == 'scout':
        from utils.crawler import Crawler
        from utils.llm import LLMClient
        crawler = Crawler()
        llm = LLMClient(load_config())
        account = flags.get('account', '影视解说')
        n = int(flags.get('n', 5))
        topics = crawler.fetch_hot_topics(top_per_source=15)
        candidates = llm.scout_topics(topics, account_type=account, n_candidates=n)
        batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        queue.save_scout_batch(batch_id, topics, candidates)
        return {'message': f'{len(topics)} 条热点 → {len(candidates)} 候选',
                'batch_id': batch_id, 'candidates': candidates}

    # ── clip（FunClip 异步）──
    if name == 'clip':
        if not pos:
            raise HTTPException(status_code=400, detail='用法: /clip 输入路径 -i "指令"')
        req2 = ClipRequest(input=pos[0], instructions=flags.get('i', flags.get('instructions', '')))
        job_id = f'clip_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        with _clip_jobs_lock:
            _clip_jobs[job_id] = {'job_id': job_id, 'input': req2.input,
                                  'instructions': req2.instructions,
                                  'status': 'running', 'created_at': datetime.now().isoformat()}
        threading.Thread(target=_run_clip, args=(job_id, req2), daemon=True).start()
        return {'message': '裁剪任务已提交（异步）', 'job_id': job_id, 'poll': f'/api/clip/{job_id}'}

    # ── 任务型：auto / fast / hot（提交任务队列，前端轮询）──
    if name in ('auto', 'fast', 'hot'):
        if not pos:
            raise HTTPException(status_code=400, detail=f'用法: /{name} 概念')
        concept = ' '.join(pos)
        opts = {'mode': name}
        if name == 'auto':
            for k in ('character', 'character_mode', 'voice', 'lut', 'bgm'):
                if flags.get(k):
                    opts[k] = flags[k]
            for k in ('duration', 'slowmo'):
                if flags.get(k):
                    opts[k] = float(flags[k])
            if flags.get('stt'):
                opts['stt'] = True
        else:  # fast / hot
            for k in ('images_source', 'effects', 'voice', 'motion', 'lut', 'bgm', 'account'):
                if flags.get(k):
                    opts[k] = flags[k]
            if flags.get('n'):
                opts['images_count'] = min(int(flags['n']), 5)  # ≤5（用户约定）
            if flags.get('stt'):
                opts['stt'] = True
        task_id = queue.submit(concept, options=opts)
        return {'message': f'任务已提交: {task_id}', 'task_id': task_id,
                'progress_url': f'/api/tasks/{task_id}'}

    raise HTTPException(status_code=400, detail=f'Tool {name} not dispatchable')


# ── 静态前端 /ui ──
_UI_DIR = os.path.join(os.path.dirname(__file__), '..', 'ui')

if os.path.isdir(_UI_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount('/ui', StaticFiles(directory=_UI_DIR, html=True), name='ui')


@app.get('/ui')
def ui_index():
    """/ui 重定向到 index.html（未挂载时兜底）"""
    index = os.path.join(_UI_DIR, 'index.html')
    if os.path.exists(index):
        return FileResponse(index, media_type='text/html')
    raise HTTPException(status_code=404, detail='ui/index.html not found')


if __name__ == '__main__':
    import uvicorn

    parser = argparse.ArgumentParser(description='Vidance API Server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8894)
    args = parser.parse_args()

    print(f'  [api] starting on {args.host}:{args.port}')
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')
