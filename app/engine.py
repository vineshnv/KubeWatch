"""Constrained read-only diagnostics over local subprocesses or SSH.
No user-defined shell commands are accepted by the API.
"""
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Optional

COMMANDS = {
    'overview': ['sh', '-c', 'uptime; echo "---MEM---"; free -m; echo "---DISK---"; df -h /'],
    'processes': ['ps', '-eo', 'pid,comm,%cpu,%mem', '--sort=-%cpu'],
    'journal': ['journalctl', '-n', '35', '--no-pager', '-p', 'warning'],
    'nodes': ['kubectl', 'get', 'nodes', '-o', 'json'],
    'pods': ['kubectl', 'get', 'pods', '-A', '-o', 'json'],
    'deployments': ['kubectl', 'get', 'deployments', '-A', '-o', 'json'],
    'events': ['kubectl', 'get', 'events', '-A', '-o', 'json'],
    'top': ['kubectl', 'top', 'nodes', '--no-headers'],
}
KUBE = {'nodes', 'pods', 'deployments', 'events', 'top'}
MAX_OUTPUT = 2_000_000

class CommandError(Exception):
    pass

class Session:
    def __init__(self, mode, client=None):
        self.id = uuid.uuid4().hex
        self.mode = mode
        self.client = client
        self.kubeconfig = None
        self.lock = threading.Lock()

    def run(self, name):
        if name not in COMMANDS:
            raise CommandError('Unsupported diagnostic operation')
        args = list(COMMANDS[name])
        if name in KUBE and self.kubeconfig:
            args[1:1] = ['--kubeconfig', self.kubeconfig]
        if self.mode == 'local':
            try:
                result = subprocess.run(args, text=True, capture_output=True, timeout=12, check=False)
                output = result.stdout[:MAX_OUTPUT]
                error = result.stderr[:1500]
                return {'ok': result.returncode == 0, 'output': output, 'error': error}
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                return {'ok': False, 'output': '', 'error': str(e)}
        with self.lock:
            try:
                command = shlex.join(args)
                _, stdout, stderr = self.client.exec_command(command, timeout=12)
                stdout.channel.settimeout(12)
                out = stdout.read(MAX_OUTPUT+1).decode('utf-8', 'replace')
                err = stderr.read(1500).decode('utf-8', 'replace')
                status = stdout.channel.recv_exit_status()
                return {'ok': status == 0, 'output': out[:MAX_OUTPUT], 'error': err}
            except Exception as e:
                return {'ok': False, 'output': '', 'error': str(e)}

    def attach_kubeconfig(self, data):
        if not data or len(data) > 1024 * 1024:
            raise CommandError('Kubeconfig must be between 1 byte and 1 MB')
        if b'apiVersion:' not in data or b'clusters:' not in data:
            raise CommandError('File does not look like a kubeconfig')
        if self.kubeconfig:
            self.remove_kubeconfig()
        if self.mode == 'local':
            fd, path = tempfile.mkstemp(prefix='kw-kube-', suffix='.yaml')
            with os.fdopen(fd, 'wb') as f: f.write(data)
            self.kubeconfig = path
        else:
            path = '/tmp/kw-kube-' + uuid.uuid4().hex + '.yaml'
            with self.lock:
                sftp = self.client.open_sftp()
                try:
                    with sftp.file(path, 'wb') as f: f.write(data)
                    sftp.chmod(path, 0o600)
                finally: sftp.close()
            self.kubeconfig = path

    def remove_kubeconfig(self):
        if not self.kubeconfig: return
        if self.mode == 'local':
            Path(self.kubeconfig).unlink(missing_ok=True)
        else:
            try:
                with self.lock:
                    sftp = self.client.open_sftp()
                    try: sftp.remove(self.kubeconfig)
                    finally: sftp.close()
            except Exception: pass
        self.kubeconfig = None

    def close(self):
        self.remove_kubeconfig()
        if self.client: self.client.close()


def connect_ssh(host, port, username, password, known_host_key=None):
    import paramiko
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', host) or not 1 <= port <= 65535:
        raise CommandError('Invalid host or port')
    if not username or not password:
        raise CommandError('Username and password required')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # Reject unknown SSH hosts. Users must register host keys in ~/.ssh/known_hosts first.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=host, port=port, username=username, password=password,
                       allow_agent=False, look_for_keys=False, timeout=8,
                       auth_timeout=8, banner_timeout=8)
    except Exception as exc:
        client.close()
        raise CommandError(f'SSH connection failed: {exc}') from exc
    return Session('ssh', client)


def parse_result(name, result):
    if name in {'nodes', 'pods', 'deployments', 'events'} and result['ok']:
        try:
            d = json.loads(result['output'])
            items = d.get('items', [])
            if name == 'nodes':
                rows = [{'name': x['metadata']['name'], 'status': next((c.get('status') for c in x.get('status', {}).get('conditions', []) if c.get('type') == 'Ready'), 'Unknown'), 'version': x.get('status', {}).get('nodeInfo', {}).get('kubeletVersion', '—')} for x in items]
            elif name == 'pods':
                rows = [{'name': x['metadata']['name'], 'namespace': x['metadata'].get('namespace', 'default'), 'status': x.get('status', {}).get('phase', 'Unknown'), 'restarts': sum(c.get('restartCount',0) for c in x.get('status', {}).get('containerStatuses', []))} for x in items]
            elif name == 'deployments':
                rows = [{'name': x['metadata']['name'], 'namespace': x['metadata'].get('namespace', 'default'), 'ready': f"{x.get('status',{}).get('readyReplicas',0)}/{x.get('spec',{}).get('replicas',1)}"} for x in items]
            else:
                rows = [{'reason': x.get('reason','—'), 'namespace': x['metadata'].get('namespace','default'), 'message': x.get('message','')[:220], 'type': x.get('type','Normal')} for x in items]
            return {**result, 'rows': rows[:250]}
        except (ValueError, KeyError, TypeError):
            return {**result, 'ok': False, 'error': 'Could not parse kubectl JSON output'}
    return result
