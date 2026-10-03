"""Direct Codex app-server RPC. Login files stay on the local machine."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import datetime as dt
from cloud_mirror import cli_executable


class RPCError(RuntimeError):
    pass


class CodexRPC:
    def __init__(self, command=None, env=None, on_event=None, on_request=None):
        self.lock = threading.Lock()
        self.pending = {}
        self.counter = 0
        self.on_event = on_event or (lambda row: None)
        self.on_request = on_request or (lambda row: None)
        self.process = subprocess.Popen(command or [cli_executable(), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'codex_suixing', 'title': 'Codex 随行', 'version': '1.4.0'},
                                 'capabilities': {'experimentalApi': True}})
        self.write({'method': 'initialized'})

    def write(self, row):
        with self.lock:
            self.process.stdin.write(json.dumps(row, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                row = json.loads(line)
                if 'method' in row:
                    if 'id' in row:
                        self.on_request(row)
                    else:
                        self.on_event(row)
                else:
                    response = self.pending.get(row.get('id'))
                    if response is not None:
                        response.put(row)
        except Exception:
            pass
        finally:
            for response in list(self.pending.values()):
                response.put({'error': {'message': 'CLI 连接中断，结果未确认'}})

    def call(self, method, params=None, timeout=25):
        with self.lock:
            self.counter += 1
            key = self.counter
            response = self.pending[key] = queue.Queue()
        try:
            self.write({'id': key, 'method': method, 'params': params or {}})
            row = response.get(timeout=timeout)
            if 'error' in row:
                raise RPCError(row['error']['message'])
            return row.get('result', {})
        finally:
            self.pending.pop(key, None)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


class NativeSession:
    def __init__(self, root, rpc_factory=CodexRPC, state_dir=None):
        self.root = Path(root)
        self.state = Path(state_dir or os.environ.get("CODEX_SUIXING_STATE_DIR") or self.root / ".state").resolve()
        self.rpc_factory = rpc_factory
        self.catalog = None
        self.models = []
        self.engines = {}
        self.loaded = {}
        self.prompts = {}
        self.state_lock = threading.RLock()
        self.allowlist = set()
        self.projects = []
        self.live_messages = {}

    def status(self, ids=None):
        if ids is not None:
            self.allowlist = set(ids)
        if self.catalog is None or self.catalog.process.poll() is not None:
            self.catalog = self.rpc_factory()
            data = self.catalog.call('model/list', {'limit': 100, 'includeHidden': False})
            self.models = [{'id': m['model'], 'label': m.get('displayName', m['model']),
                            'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])]}
                           for m in data.get('data', [])]
        account = self.catalog.call('account/read', {'refreshToken': False})
        with self.state_lock:
            return {'connected': bool(account.get('account')) or not account.get('requiresOpenaiAuth', True),
                    'threadIds': list(self.allowlist), 'models': self.models, 'transport': 'cli',
                    'prompts': list(self.prompts.values())}

    def event(self, engine, row):
        params = row.get('params', {})
        tid = params.get('threadId')
        if row['method'] in ('item/started', 'item/completed'):
            item = params.get('item', {})
            if item.get('type') in ('fileChange', 'commandExecution'):
                engine.items[item.get('id')] = item
            if item.get('type') == 'agentMessage' and item.get('phase') in (None, 'commentary', 'final'):
                with self.state_lock:
                    self.live_messages.setdefault(tid, {})[item['id']] = {'role': 'assistant', 'phase': item.get('phase') or 'final',
                        'text': item.get('text', ''), 'nativeItemId': item['id'], 'time': dt.datetime.now(dt.timezone.utc).isoformat()}
        if row['method'] == 'item/agentMessage/delta':
            with self.state_lock:
                message = self.live_messages.get(tid, {}).get(params.get('itemId'))
                if message:
                    message['text'] += params.get('delta', '')
        if row['method'] == 'turn/started':
            engine.turn_active = True
        if row['method'] == 'turn/completed':
            engine.turn_active = False
            tid = row.get('params', {}).get('threadId')
            generation = engine.generation
            # Release the writer when idle, so Desktop can continue the same history.
            def release():
                with self.state_lock:
                    if self.engines.get(tid) is not engine or engine.generation != generation:
                        return
                    self.engines.pop(tid, None)
                    self.loaded.pop(tid, None)
                    self.live_messages.pop(tid, None)
                    for key in [k for k, p in self.prompts.items() if p['threadId'] == tid]:
                        self.prompts.pop(key, None)
                engine.close()
            threading.Timer(2, release).start()

    def request(self, engine, row):
        params = row.get('params', {})
        tid = params.get('threadId', '')
        key = str(row['id']) + ':' + str(engine.process.pid)
        method = row['method']
        if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval', 'item/tool/requestUserInput', 'item/permissions/requestApproval'):
            if method == 'item/tool/requestUserInput':
                public = {'type': 'question', 'questions': params.get('questions', [])}
            else:
                item = engine.items.get(params.get('itemId'), {})
                if 'fileChange' in method:
                    detail = '\n\n'.join(str(change.get('path', '')) + '\n' + str(change.get('diff', '')) for change in item.get('changes', []))
                else:
                    detail = str(params.get('command') or item.get('command') or params.get('permissions') or '')
                detail = '\n\n'.join(part for part in (str(params.get('cwd') or ''), detail, str(params.get('reason') or '')) if part)
                public = {'type': 'approval', 'title': '文件修改' if 'fileChange' in method else '权限请求' if 'permissions' in method else '运行命令',
                          'detail': detail or '请确认这项操作'}
            with self.state_lock:
                self.prompts[key] = {'id': key, 'threadId': tid, **public}
                engine.requests[key] = row
        else:
            engine.write({'id': row['id'], 'error': {'code': -32601, 'message': 'This client does not support this request'}})

    def new_engine(self):
        holder = []
        engine = self.rpc_factory(on_event=lambda row: self.event(holder[0], row) if holder else None,
                                  on_request=lambda row: self.request(holder[0], row) if holder else None)
        engine.requests = {}
        engine.items = {}
        engine.generation = 0
        engine.turn_active = False
        holder.append(engine)
        return engine

    def resolve_prompt(self, input):
        key = input.get('promptId', '')
        tid = input.get('threadId')
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', input.get('id', '')):
            return {'status': 'failed', 'detail': '无效请求编号'}
        with self.state_lock:
            prompt = self.prompts.get(key)
            engine = self.engines.get(tid)
            if not prompt or prompt['threadId'] != tid or engine is None or key not in engine.requests:
                return {'status': 'failed', 'detail': '请求已结束，请刷新后查看'}
            row = engine.requests[key]
            if prompt['type'] == 'question':
                answers = input.get('answers', {})
                expected = {q['id'] for q in prompt['questions']}
                if set(answers) != expected or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in answers.values()):
                    return {'status': 'failed', 'detail': '请回答所有问题'}
                result = {'answers': {key: {'answers': [value]} for key, value in answers.items()}}
            else:
                allow = input.get('decision') == 'accept'
                if input.get('decision') not in ('accept', 'decline'):
                    return {'status': 'failed', 'detail': '无效的审批选择'}
                if row['method'] == 'item/permissions/requestApproval':
                    result = {'permissions': row['params'].get('permissions', {}) if allow else {}, 'scope': 'turn'}
                else:
                    result = {'decision': 'accept' if allow else 'decline'}
            engine.write({'id': row['id'], 'result': result})
            self.prompts.pop(key, None)
            engine.requests.pop(key, None)
            return {'status': 'sent', 'detail': '选择已提交给 CLI'}

    def send(self, input):
        tid = input.get('threadId')
        if input.get('mode') == 'decision':
            return self.resolve_prompt(input)
        if input.get('mode') not in ('create', 'send') or not isinstance(input.get('text'), str) or not 0 < len(input['text']) <= 8000:
            return {'status': 'failed', 'detail': '无效消息'}
        if input['mode'] == 'send' and tid not in self.allowlist:
            return {'status': 'failed', 'detail': '此对话未在本机同步列表中'}
        overrides = {}
        if 'model' in input or 'thinking' in input:
            option = next((m for m in self.models if m['id'] == input.get('model')), None)
            if not option or ('thinking' in input and input['thinking'] not in option['efforts']):
                return {'status': 'failed', 'detail': '模型或思考强度不匹配'}
            overrides = {'model': option['id'], **({'effort': input['thinking']} if 'thinking' in input else {})}
        items = [{'type': 'text', 'text': input['text']}]
        for path in input.get('imagePaths', []):
            file = Path(path)
            if file.resolve().parent != (self.state / 'incoming').resolve() or not re.fullmatch(r'[0-9a-f]{64}\.(jpg|png)', file.name) or not file.is_file():
                return {'status': 'failed', 'detail': '图片文件无效'}
            items.append({'type': 'localImage', 'path': str(file.resolve())})
        engine = None
        submitted = False
        try:
            if input['mode'] == 'create':
                params = {'cwd': str(self.state / 'tasks'), **({'model': overrides['model']} if overrides else {})}
                Path(params['cwd']).mkdir(parents=True, exist_ok=True)
                if input.get('projectId'):
                    project = next((p for p in self.projects if p['projectId'] == input['projectId']), None)
                    if not project or not Path(project['path']).is_dir():
                        return {'status': 'failed', 'detail': '所选项目不可用'}
                    params.update(cwd=project['path'], projectId=project['projectId'])
                    if project.get('isGitRepository'):
                        worktree = self.state / 'worktrees' / input['id']
                        worktree.parent.mkdir(parents=True, exist_ok=True)
                        result = subprocess.run(['git', '-C', project['path'], 'worktree', 'add', '--detach', str(worktree), 'HEAD'],
                                                capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        if result.returncode:
                            return {'status': 'failed', 'detail': '无法创建项目工作区，请在电脑检查 Git 状态'}
                        params['cwd'] = str(worktree)
                engine = self.new_engine()
                meta = engine.call('thread/start', params)
                tid = meta['thread']['id']
                self.allowlist.add(tid)
                if input.get('title'):
                    try:
                        engine.call('thread/name/set', {'threadId': tid, 'name': input['title'][:100]})
                    except RPCError:
                        pass
            else:
                with self.state_lock:
                    engine = self.engines.get(tid)
                    if engine is not None:
                        engine.generation += 1  # Cancel a pending idle-release timer.
                if engine is None or engine.process.poll() is not None:
                    engine = self.new_engine()
                    meta = engine.call('thread/resume', {'threadId': tid, 'excludeTurns': True})
                else:
                    meta = self.loaded[tid]
            with self.state_lock:
                self.engines[tid] = engine
                self.loaded[tid] = meta
                engine.generation += 1
            submitted = True
            result = engine.call('turn/start', {'threadId': tid, 'input': items, 'clientUserMessageId': input['id'], **overrides})
            return {'status': 'sent', 'detail': 'CLI 已接收，开始处理' if input['mode'] == 'create' else '已直接发送到原对话',
                    'resultThreadId': tid if input['mode'] == 'create' else '', 'turnId': result.get('turn', {}).get('id', '')}
        except RPCError as error:
            if engine is not None and not engine.turn_active:
                self.engines.pop(tid, None)
                self.loaded.pop(tid, None)
                engine.close()
            if 'active writer' in str(error):
                return {'status': 'failed', 'detail': '桌面正在持有此对话，CLI 暂不能接管。请在手机新建任务，或等桌面释放此对话后再发；没有通过其他任务转发。'}
            return {'status': 'failed', 'detail': 'CLI 未接受消息，请检查本机登录和对话状态'}
        except Exception:
            return {'status': 'uncertain' if submitted else 'failed', 'detail': 'CLI 结果未确认，未自动重发' if submitted else 'CLI 暂时不可用'}
