"""FastAPI server for local/SSH read-only Linux and Kubernetes diagnostics."""
import os
import secrets
import time
from pathlib import Path
from threading import Lock
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .engine import Session, connect_ssh, CommandError, parse_result, COMMANDS

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title='KubeWatch', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
SESSIONS = {}
LOCK = Lock()
MAX_SESSIONS = 10
TTL = 3600

class ConnectRequest(BaseModel):
    mode: str = 'local'
    host: str = ''
    port: int = Field(default=22, ge=1, le=65535)
    username: str = ''
    password: str = ''


def get_session(token):
    with LOCK:
        record = SESSIONS.get(token)
        if not record: raise HTTPException(401, 'Not connected')
        session, last = record
        if time.time() - last > TTL:
            del SESSIONS[token]
            session.close()
            raise HTTPException(401, 'Session expired; reconnect')
        SESSIONS[token] = (session, time.time())
        return session


def token_from(request):
    token = request.headers.get('X-Session', '')
    if not token: raise HTTPException(401, 'Connect first')
    return token

@app.get('/')
def index():
    return FileResponse(ROOT/'static'/'index.html')

@app.post('/api/connect')
def connect(req: ConnectRequest):
    if req.mode not in ('local','ssh'):
        raise HTTPException(400, 'Mode must be local or ssh')
    with LOCK:
        if len(SESSIONS) >= MAX_SESSIONS:
            raise HTTPException(429, 'Too many active sessions')
    try:
        session = Session('local') if req.mode == 'local' else connect_ssh(req.host, req.port, req.username, req.password)
    except CommandError as e:
        raise HTTPException(400, str(e))
    token = secrets.token_urlsafe(32)
    with LOCK: SESSIONS[token] = (session, time.time())
    return {'token': token, 'mode': session.mode, 'message': 'Connected'}

@app.post('/api/kubeconfig')
async def kubeconfig(request: Request, file: UploadFile = File(...)):
    session = get_session(token_from(request))
    data = await file.read(1024*1024+1)
    try: session.attach_kubeconfig(data)
    except CommandError as e: raise HTTPException(400, str(e))
    return {'ok': True, 'filename': file.filename, 'message': 'Kubeconfig staged for this session only'}

@app.get('/api/diagnostics/{name}')
def diagnostics(name: str, request: Request):
    session = get_session(token_from(request))
    if name not in COMMANDS: raise HTTPException(404, 'Unknown diagnostic')
    return parse_result(name,session.run(name))

@app.get('/api/log-alerts')
def log_alerts(request: Request):
    """Apply fixed signature rules to recent systemd warning/error log entries."""
    session = get_session(token_from(request))
    raw = session.run('journal')
    if not raw['ok']:
        return {'ok': False, 'alerts': [], 'error': raw['error'] or 'Journal unavailable'}
    signatures = {'authentication': ('authentication failure','failed password','invalid user'),
                  'resource': ('out of memory','oom-killer','no space left'),
                  'service': ('failed to start','segmentation fault','service failed'),
                  'network': ('connection refused','network unreachable','timed out')}
    matches = []
    for line in raw['output'].splitlines():
        lower = line.lower()
        category = next((key for key, patterns in signatures.items() if any(p in lower for p in patterns)),None)
        if category: matches.append({'category':category, 'message':line[:400]})
    return {'ok': True, 'alerts': matches[-35:], 'scanned_lines': len(raw['output'].splitlines())}

@app.post('/api/disconnect')
def disconnect(request: Request):
    token = token_from(request)
    with LOCK: record = SESSIONS.pop(token, None)
    if record: record[0].close()
    return {'ok': True}

@app.on_event('shutdown')
def shutdown():
    with LOCK:
        for session, _ in SESSIONS.values(): session.close()
        SESSIONS.clear()

@app.middleware('http')
async def local_only(request: Request, call_next):
    # Reject non-local Host headers.
    host = request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','[::1]','testserver'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Access this app via localhost only'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    return response
