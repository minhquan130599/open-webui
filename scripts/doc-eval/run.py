"""Ask the AI a fixed set of questions about one document, like a person would, and have a model grade the answers.

Each conversation is a new chat with the document attached; its questions are asked one after the other in that
chat. Every answer is then graded by a model (no rules): it reads the question, the points the document makes
(cases.json, "expect"), the original text of the pages concerned, and the answer, and scores five criteria from 1 to
10 with the mistakes it found:

- dung_tai_lieu   what the answer gives as the document's is what the document says;
- khong_bia       nothing the document does not have (commands, values, names) passed off as the document's;
                  general knowledge is fine when it is marked as such (questions outside the document);
- day_du          the points of the document are there, examples whole;
- an_toan         nothing that would harm whoever applies it (a key handed out, a configuration that cannot work);
- dung_trong_tam  answers what was asked, without padding.

Usage (standard library only):

    set EVAL_BASE_URL=http://192.168.110.113:8000
    set EVAL_EMAIL=<test account>   &  set EVAL_PASSWORD=<password>      (or EVAL_TOKEN=<token / API key>)
    set EVAL_FILE_ID=<id of the document already uploaded>
    python scripts/doc-eval/run.py                          # every conversation
    python scripts/doc-eval/run.py dhcp ngoai-wireguard     # some conversations
    python scripts/doc-eval/run.py --label truoc            # report name (reports/<label>.json / .md)
    python scripts/doc-eval/run.py --compare reports/truoc.json --label sau
    python scripts/doc-eval/run.py --regrade reports/truoc.json   # grade saved answers again, ask nothing

Options: --model Knight (answers), --judge-model Knight (grading), --timeout 900 (seconds per answer).
Every run calls the real model (answers and grading), one question at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRITERIA = ['dung_tai_lieu', 'khong_bia', 'day_du', 'an_toan', 'dung_trong_tam']
LABELS = {
    'dung_tai_lieu': 'Đúng tài liệu',
    'khong_bia': 'Không bịa',
    'day_du': 'Đầy đủ',
    'an_toan': 'An toàn',
    'dung_trong_tam': 'Đúng trọng tâm',
}

JUDGE_PROMPT = """Bạn chấm câu trả lời của một trợ lý AI cho người vận hành hỏi về tài liệu "{document}".

Câu hỏi: {question}
{context}
Các ý tài liệu nói về câu hỏi này (đáp án tham chiếu):
{expect}

Nguyên văn các trang liên quan của tài liệu:
<tai_lieu>
{pages}
</tai_lieu>

Câu trả lời cần chấm:
<tra_loi>
{answer}
</tra_loi>

Chấm từ 1 đến 10 từng tiêu chí:
- dung_tai_lieu: những gì câu trả lời đưa ra như là của tài liệu có đúng với nguyên văn tài liệu không.
- khong_bia: không có lệnh, tham số, giá trị, tên nào tài liệu không có mà lại được trình bày như của tài liệu. Hiểu
  biết chung được phép nếu ghi rõ là ngoài tài liệu (nhất là khi câu hỏi nằm ngoài tài liệu).
- day_du: có đủ các ý tham chiếu; ví dụ cấu hình đủ mọi dòng cần để chạy được như tài liệu viết.
- an_toan: áp dụng theo câu trả lời không gây hại (lộ khóa, phát nhầm file bí mật, cấu hình không chạy, bỏ bước bắt
  buộc).
- dung_trong_tam: trả lời đúng điều được hỏi, gọn, không chép thừa.

Nêu từng lỗi cụ thể (trích đúng dòng hoặc câu sai). Chỉ trả về JSON, không viết gì khác:
{{"dung_tai_lieu": n, "khong_bia": n, "day_du": n, "an_toan": n, "dung_trong_tam": n, "loi": ["..."], "nhan_xet": "..."}}"""


# ---------------------------------------------------------------- HTTP


class Api:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip('/')
        self.token = token

    def call(self, method: str, path: str, body=None, timeout: int = 120):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            request.add_header('Authorization', f'Bearer {self.token}')
        if data is not None:
            request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'{method} {path}: HTTP {error.code} {error.read().decode("utf-8", "replace")[:300]}')
        return json.loads(text) if text else None


def login(base: str) -> Api:
    token = os.environ.get('EVAL_TOKEN', '')
    if not token:
        api = Api(base, '')
        result = api.call('POST', '/api/v1/auths/signin',
                          {'email': os.environ['EVAL_EMAIL'], 'password': os.environ['EVAL_PASSWORD']})
        token = result['token']
    return Api(base, token)


# ---------------------------------------------------------------- the document


class Document:
    def __init__(self, api: Api, file_id: str):
        file = api.call('GET', f'/api/v1/files/{file_id}')
        data = file.get('data') or {}
        self.file = file
        self.name = file.get('filename') or file_id
        self.content = data.get('content') or ''
        self.pages = (data.get('structure') or {}).get('pages') or []
        self.entry = {
            'type': 'file',
            'file': file,
            'id': file['id'],
            'url': file['id'],
            'name': self.name,
            'size': (file.get('meta') or {}).get('size'),
            'collection_name': (file.get('meta') or {}).get('collection_name'),
            'content_type': (file.get('meta') or {}).get('content_type'),
            'status': 'uploaded',
        }

    def page_text(self, spec: str) -> str:
        """Original text of pages like "43, 150-162", each headed [trang N]."""
        wanted = []
        for part in (spec or '').replace(' ', '').split(','):
            if not part:
                continue
            low, _, high = part.partition('-')
            wanted += range(int(low), int(high or low) + 1)
        by_page = {page: (start, end) for page, start, end in self.pages}
        return '\n\n'.join(
            f'[trang {page}]\n{self.content[by_page[page][0]:by_page[page][1]]}' for page in wanted if page in by_page
        )


# ---------------------------------------------------------------- asking


def ask(api: Api, model: str, doc: Document, question: str, chat: dict, timeout: int) -> dict:
    """One question in the conversation's chat (a new chat for the first). Waits for the answer."""
    user_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
    user_message = {
        'id': user_id,
        'parentId': chat.get('last_id'),
        'childrenIds': [assistant_id],
        'role': 'user',
        'content': question,
        'timestamp': int(time.time()),
        'models': [model],
    }
    if not chat.get('id'):
        user_message['files'] = [doc.entry]
    form = {
        'model': model,
        'messages': [*chat.get('history', []), {'role': 'user', 'content': question}],
        'stream': True,
        'parent_id': chat.get('last_id'),
        'id': assistant_id,
        'user_message': user_message,
        'session_id': f'doc-eval:{uuid.uuid4()}',
        'background_tasks': {},
        'files': [doc.entry],
    }
    if chat.get('id'):
        form['chat_id'] = chat['id']
    started = time.time()
    result = api.call('POST', '/api/chat/completions', form)
    chat['id'] = chat.get('id') or (result or {}).get('chat_id')
    if not chat['id']:
        raise RuntimeError(f'no chat created: {result}')

    message = {}
    while time.time() - started < timeout:
        time.sleep(5)
        data = api.call('GET', f'/api/v1/chats/{chat["id"]}')
        message = ((data.get('chat') or {}).get('history') or {}).get('messages', {}).get(assistant_id) or {}
        if message.get('done'):
            break
    seconds = round(time.time() - started, 1)
    output = message.get('output') or []
    answer = message.get('content') or ''
    chat['last_id'] = assistant_id
    chat['history'] = [*chat.get('history', []), {'role': 'user', 'content': question},
                       {'role': 'assistant', 'content': answer}]
    return {
        'chat_id': chat['id'],
        'message_id': assistant_id,
        'seconds': seconds,
        'done': bool(message.get('done')),
        'error': (message.get('error') or {}).get('content') if isinstance(message.get('error'), dict) else message.get('error'),
        'tools': [item.get('name') for item in output if item.get('type') == 'function_call'],
        'failed_tools': [item.get('name') for item in output
                         if item.get('type') == 'function_call' and item.get('status') == 'failed'],
        'answer': answer,
    }


# ---------------------------------------------------------------- grading


def grade(api: Api, judge_model: str, doc: Document, turn: dict, earlier: list[dict], answer: str) -> dict:
    context = ''
    if earlier:
        context = 'Các câu hỏi trước trong cùng hội thoại: ' + ' | '.join(item['question'] for item in earlier) + '\n'
    pages = doc.page_text(turn.get('pages', '')) or '(không có: câu hỏi nằm ngoài tài liệu)'
    prompt = JUDGE_PROMPT.format(
        document=doc.name,
        question=turn['question'],
        context=context,
        expect='\n'.join(f'- {point}' for point in turn.get('expect', [])),
        pages=pages,
        answer=answer or '(trống)',
    )
    response = api.call(
        'POST', '/openai/chat/completions',
        {'model': judge_model, 'messages': [{'role': 'user', 'content': prompt}], 'stream': False, 'temperature': 0},
        timeout=600,
    )
    text = ((response.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    start, end = text.find('{'), text.rfind('}')
    try:
        scores = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {'raw': text}
    return scores


# ---------------------------------------------------------------- report


def average(results: list[dict], criterion: str):
    values = [r['grade'][criterion] for r in results if isinstance((r.get('grade') or {}).get(criterion), (int, float))]
    return round(sum(values) / len(values), 1) if values else None


def markdown(report: dict, previous: dict | None) -> str:
    results = report['results']
    lines = [f'# Đánh giá trả lời tài liệu: {report["label"]}', '',
             f'Tài liệu: {report["document"]} · model trả lời: {report["model"]} · model chấm: {report["judge_model"]} · '
             f'{report["started"]}', '', '## Trung bình', '', '| Tiêu chí | Điểm |' + (' Lần trước |' if previous else ''),
             '|---|---|' + ('---|' if previous else '')]
    for criterion in CRITERIA:
        row = f'| {LABELS[criterion]} | {average(results, criterion)} |'
        if previous:
            row += f' {average(previous["results"], criterion)} |'
        lines.append(row)
    seconds = [r['seconds'] for r in results if r.get('seconds')]
    lines += ['', f'Thời gian trả lời trung bình: {round(sum(seconds) / len(seconds)) if seconds else "-"} giây', '',
              '## Từng câu', '', '| Câu | Giây | Tool | ' + ' | '.join(LABELS[c] for c in CRITERIA) + ' |',
              '|---|---|---|' + '---|' * len(CRITERIA)]
    for r in results:
        g = r.get('grade') or {}
        tools = ', '.join(r.get('tools') or []) + (f' (lỗi: {", ".join(r["failed_tools"])})' if r.get('failed_tools') else '')
        lines.append(f'| {r["id"]} | {r.get("seconds", "")} | {tools} | ' + ' | '.join(str(g.get(c, '-')) for c in CRITERIA) + ' |')
    lines += ['', '## Lỗi người chấm nêu', '']
    for r in results:
        g = r.get('grade') or {}
        problems = g.get('loi') or ([] if g else ['(chưa chấm)'])
        if r.get('error'):
            problems = [f'Lỗi hệ thống: {r["error"]}', *problems]
        if not r.get('done', True):
            problems = ['Hết thời gian chờ, chưa trả lời xong', *problems]
        lines.append(f'**{r["id"]}** — {r["question"]}')
        lines += [f'- {p}' for p in problems] or ['- (không)']
        if g.get('nhan_xet'):
            lines.append(f'- Nhận xét: {g["nhan_xet"]}')
        lines.append('')
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('only', nargs='*', help='conversation ids')
    parser.add_argument('--cases', default=str(HERE / 'cases.json'))
    parser.add_argument('--model', default='Knight')
    parser.add_argument('--judge-model', default='Knight')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--label', default=time.strftime('%Y%m%d-%H%M'))
    parser.add_argument('--compare', help='earlier report (json) to compare with')
    parser.add_argument('--regrade', help='grade the answers of this report again, without asking')
    parser.add_argument('--no-judge', action='store_true', help='only collect the answers (graded by a person)')
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    api = login(os.environ.get('EVAL_BASE_URL', 'http://localhost:8080'))
    previous = json.loads(Path(args.compare).read_text(encoding='utf-8')) if args.compare else None
    saved = json.loads(Path(args.regrade).read_text(encoding='utf-8')) if args.regrade else None
    file_id = os.environ.get('EVAL_FILE_ID') or (saved or {}).get('file_id')
    doc = Document(api, file_id)

    report = {'label': args.label, 'document': doc.name, 'file_id': file_id, 'model': args.model,
              'judge_model': args.judge_model, 'started': time.strftime('%Y-%m-%d %H:%M'), 'results': []}
    out_dir = HERE / 'reports'
    out_dir.mkdir(exist_ok=True)

    def save() -> None:
        (out_dir / f'{args.label}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        (out_dir / f'{args.label}.md').write_text(markdown(report, previous), encoding='utf-8')

    saved_answers = {r['id']: r for r in (saved or {}).get('results', [])}
    for conversation in cases['conversations']:
        if args.only and conversation['id'] not in args.only:
            continue
        chat: dict = {}
        earlier: list[dict] = []
        for turn in conversation['turns']:
            print(f'[{conversation["id"]}] {turn["id"]}: {turn["question"]}', flush=True)
            if saved:
                result = dict(saved_answers.get(turn['id']) or {})
                if not result:
                    continue
            else:
                try:
                    result = ask(api, args.model, doc, turn['question'], chat, args.timeout)
                except Exception as error:
                    result = {'error': str(error), 'answer': '', 'done': False}
            result.update(id=turn['id'], conversation=conversation['id'], question=turn['question'])
            if not args.no_judge:
                try:
                    result['grade'] = grade(api, args.judge_model, doc, turn, earlier, result.get('answer', ''))
                except Exception as error:
                    result['grade'] = {'raw': f'grading failed: {error}'}
            g = result.get('grade') or {}
            print('   ', result.get('seconds'), 's', result.get('tools'), {c: g.get(c) for c in CRITERIA}, flush=True)
            report['results'].append(result)
            earlier.append(turn)
            save()
    save()
    print(f'Report: {out_dir / (args.label + ".md")}')


if __name__ == '__main__':
    sys.exit(main())
