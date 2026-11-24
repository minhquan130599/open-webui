"""Ask the AI a fixed set of questions in a test channel and check how it worked, not only what it wrote.

Each case is posted in the channel as "@<model> <question>" (optionally with a file), like a person would. The reply
keeps the whole tool-call trace (data.output), which is checked for the mistakes seen so far:

- the answer written twice (the model answered, called one more tool, then answered again);
- the same file attached twice (an export repeated);
- tools that must or must not be used (a report exported with export_project_report, never through the terminal);
- failed tool calls.

Usage (only the standard library is needed):

    set EVAL_BASE_URL=http://192.168.110.113:8000
    set EVAL_TOKEN=<API key or token of a test account>
    set EVAL_CHANNEL_ID=<id of a channel kept for tests: every case posts in it>
    python scripts/agent-eval/run.py                 # every case
    python scripts/agent-eval/run.py export-docx     # some cases
    python scripts/agent-eval/run.py --repeat 3      # each case 3 times (models do not answer the same way twice)

Options: --model Knight (the model mentioned), --cases <file>, --timeout <seconds per case>, --json <report file>.
Each run calls the real model: it costs model time and posts messages in the channel.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANSWER_MIN_CHARS = 400  # a message this long is an answer, not "now I will export the file"


# ---------------------------------------------------------------- HTTP


class Api:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip('/')
        self.token = token

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, content_type: str = 'application/json'):
        data = raw if raw is not None else (json.dumps(body).encode('utf-8') if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header('Authorization', f'Bearer {self.token}')
        if data is not None:
            request.add_header('Content-Type', content_type)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'{method} {path}: HTTP {error.code} {error.read().decode("utf-8", "replace")[:300]}')
        return json.loads(text) if text else None

    def upload(self, path: Path, channel_id: str) -> dict:
        boundary = uuid.uuid4().hex
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
            f'{json.dumps({"channel_id": channel_id})}\r\n'.encode('utf-8'),
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8'),
            path.read_bytes(),
            f'\r\n--{boundary}--\r\n'.encode('utf-8'),
        ]
        file = self.call('POST', '/api/v1/files/?process=true&process_in_background=false', raw=b''.join(parts),
                         content_type=f'multipart/form-data; boundary={boundary}')
        return {'type': 'file', 'id': file['id'], 'url': file['id'], 'name': path.name, 'file': file,
                'content_type': content_type, 'status': 'uploaded'}


# ---------------------------------------------------------------- one case


def ask(api: Api, channel_id: str, model: str, case: dict, timeout: int) -> dict:
    files = []
    for name in case.get('files') or []:
        path = (HERE / name) if not Path(name).is_absolute() else Path(name)
        if not path.is_file():
            return {'skipped': f'missing file {path}'}
        files.append(api.upload(path, channel_id))
    content = f'<@M:{model}|{model}> {case["prompt"]}'
    question = api.call('POST', f'/api/v1/channels/{channel_id}/messages/post',
                        {'content': content, 'data': {'files': files} if files else {}})
    started = time.time()
    while time.time() - started < timeout:
        time.sleep(5)
        replies = api.call('GET', f'/api/v1/channels/{channel_id}/messages/{question["id"]}/thread?skip=0&limit=20')
        for reply in replies or []:
            if reply['id'] == question['id'] or not (reply.get('meta') or {}).get('model_id'):
                continue
            if (reply.get('meta') or {}).get('done'):
                full = api.call('GET', f'/api/v1/channels/{channel_id}/messages/{reply["id"]}')
                full['seconds'] = round(time.time() - started)
                return full
    return {'timeout': timeout, 'question_id': question['id']}


def analyse(reply: dict) -> dict:
    data = reply.get('data') if isinstance(reply.get('data'), dict) else {}
    output = data.get('output') or []
    calls = [o for o in output if o.get('type') == 'function_call']
    results = {o.get('call_id'): o for o in output if o.get('type') == 'function_call_output'}
    answers = 0
    for item in output:
        if item.get('type') == 'message':
            text = ''.join(p.get('text') or '' for p in item.get('content') or [] if isinstance(p, dict))
            answers += len(text.strip()) >= ANSWER_MIN_CHARS
    failed = [c.get('name') for c in calls
              if c.get('status') == 'failed' or (results.get(c.get('call_id')) or {}).get('status') == 'failed']
    names = [f.get('name') or '' for f in data.get('files') or []]
    tool_text = '\n'.join(
        ''.join(str(p.get('text') or '') for p in (o.get('output') or []) if isinstance(p, dict))
        if isinstance(o.get('output'), list) else str(o.get('output') or '')
        for o in output if o.get('type') == 'function_call_output'
    )
    return {
        'answers': answers,
        'tools': [c.get('name') for c in calls if not c.get('hidden')],
        'failed': failed,
        'files': names,
        'duplicate_files': sorted({n for n in names if names.count(n) > 1}),
        'seconds': reply.get('seconds'),
        'tool_text': tool_text,
        'answer_text': reply.get('content') or '',
    }


def check(case: dict, found: dict) -> list[str]:
    expect = case.get('expect') or {}
    problems = []
    if found['answers'] > expect.get('max_answers', 1):
        problems.append(f'answered {found["answers"]} times')
    if found['answers'] < expect.get('min_answers', 1):
        problems.append('no full answer')
    if found['duplicate_files']:
        problems.append(f'same file attached twice: {", ".join(found["duplicate_files"])}')
    for name, count in (expect.get('call_counts') or {}).items():
        made = found['tools'].count(name)
        if made != count:
            problems.append(f'{name} called {made} times (expected {count})')
    for name in expect.get('must_not_call') or []:
        if name in found['tools']:
            problems.append(f'called {name}')
    if len(found['failed']) > expect.get('max_failed_tools', 0):
        problems.append(f'failed tools: {", ".join(found["failed"])}')
    for pattern in expect.get('tool_output_must_not_match') or []:
        hit = re.search(pattern, found.get('tool_text', ''), re.I | re.M)
        if hit:
            problems.append(f'tool output shows {hit.group(0)[:80]!r}')
    for pattern in expect.get('answer_must_not_match') or []:
        hit = re.search(pattern, found.get('answer_text', ''), re.I | re.M)
        if hit:
            problems.append(f'answer shows {hit.group(0)[:80]!r}')
    for extension in expect.get('file_types') or []:
        if not any(n.lower().endswith('.' + extension) for n in found['files']):
            problems.append(f'no .{extension} file attached')
    return problems


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ids', nargs='*', help='case ids to run (default: all)')
    parser.add_argument('--cases', default=str(HERE / 'cases.json'))
    parser.add_argument('--model', default=os.getenv('EVAL_MODEL', 'Knight'))
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--json', help='write the full results here')
    args = parser.parse_args()

    base, token, channel = (os.getenv(k, '') for k in ('EVAL_BASE_URL', 'EVAL_TOKEN', 'EVAL_CHANNEL_ID'))
    if not (base and token and channel):
        print('Set EVAL_BASE_URL, EVAL_TOKEN and EVAL_CHANNEL_ID (a channel kept for tests).')
        return 2
    cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    if args.ids:
        cases = [c for c in cases if c['id'] in args.ids]
    api = Api(base, token)
    rows, failures = [], 0
    for case in cases:
        for run in range(1, args.repeat + 1):
            label = case['id'] + (f' #{run}' if args.repeat > 1 else '')
            print(f'… {label}', flush=True)
            try:
                reply = ask(api, channel, args.model, case, args.timeout)
            except Exception as error:
                reply = {'error': str(error)}
            if 'skipped' in reply or 'timeout' in reply or 'error' in reply:
                status = reply.get('skipped') and f'skipped ({reply["skipped"]})' or (
                    f'timeout after {reply["timeout"]}s' if 'timeout' in reply else f'error: {reply["error"]}')
                found, problems = {}, [] if 'skipped' in reply else [status]
            else:
                found = analyse(reply)
                problems = check(case, found)
                status = 'ok' if not problems else 'FAIL: ' + '; '.join(problems)
            failures += bool(problems)
            rows.append({'case': label, 'status': status, **{k: v for k, v in found.items() if not k.endswith('_text')}})
            print(f'  {status}')
            if found:
                print(f'  tools: {", ".join(found["tools"]) or "-"} | files: {", ".join(found["files"]) or "-"}'
                      f' | {found.get("seconds")}s')
    print(f'\n{len(rows) - failures}/{len(rows)} passed')
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
