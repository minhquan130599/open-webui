"""Change NCS AI settings from the monitor page.

Unlock with MONITOR_REVEAL_KEY + a Google Authenticator code (each code works once); that opens an edit session
of SESSION_SECONDS for this browser. Only the settings listed in SETTINGS can be changed. They are written to the
config table and `monitor.config_version` is bumped so every NCS AI worker applies them (config_watch.py).
Every change is recorded as a perf_event of kind 'config_change' (secrets are never stored in it).
"""

import json
import logging
import secrets
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from open_webui.monitoring.server import security

log = logging.getLogger('ncs.monitor')
router = APIRouter()

SESSION_SECONDS = 15 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 600
VERSION_KEY = 'monitor.config_version'

# key, label, type (text | secret | bool | int | select), group, help, options
SETTINGS: list[dict[str, Any]] = [
    # Tracker (database values override .env)
    {'key': 'tracker.model', 'label': 'Model ưu tiên', 'type': 'text', 'group': 'tracker',
     'help': 'Tên model, có thể nhiều model theo thứ tự ưu tiên: nai,gpt-4o-mini. Trống = tự chọn.'},
    {'key': 'tracker.llm_failover', 'label': 'Tự chuyển kết nối khi lỗi', 'type': 'bool', 'group': 'tracker'},
    {'key': 'tracker.max_concurrency', 'label': 'Số kênh tổng hợp cùng lúc', 'type': 'int', 'group': 'tracker', 'min': 1, 'max': 20},
    {'key': 'tracker.idle_seconds', 'label': 'Kênh im lặng bao lâu thì tổng hợp (giây)', 'type': 'int', 'group': 'tracker', 'min': 10, 'max': 3600},
    {'key': 'tracker.batch_size', 'label': 'Đủ bao nhiêu tin thì tổng hợp ngay', 'type': 'int', 'group': 'tracker', 'min': 1, 'max': 500},
    # Background tasks
    {'key': 'task.model.default', 'label': 'Model tác vụ nền (model nội bộ)', 'type': 'text', 'group': 'task',
     'help': 'ID model dùng để đặt tiêu đề, gợi ý, tag. Trống = dùng model đang chat.'},
    {'key': 'task.model.external', 'label': 'Model tác vụ nền (model kết nối ngoài)', 'type': 'text', 'group': 'task'},
    # Knowledge: embedding
    {'key': 'rag.bypass_embedding_and_retrieval', 'label': 'Bỏ qua embedding và truy xuất', 'type': 'bool', 'group': 'embedding',
     'help': 'Bật = chỉ trích xuất chữ, không tạo vector. Đổi model embedding xong phải lập chỉ mục lại trong NCS AI.'},
    {'key': 'rag.embedding_engine', 'label': 'Engine embedding', 'type': 'select', 'group': 'embedding',
     'options': [['', 'Local (sentence-transformers)'], ['openai', 'API dạng OpenAI'], ['ollama', 'Ollama'], ['azure_openai', 'Azure OpenAI']]},
    {'key': 'rag.embedding_model', 'label': 'Model embedding', 'type': 'text', 'group': 'embedding'},
    {'key': 'rag.openai.api_base_url', 'label': 'URL API embedding', 'type': 'text', 'group': 'embedding', 'help': 'Kết thúc bằng /v1'},
    {'key': 'rag.openai.api_key', 'label': 'Khóa API embedding', 'type': 'secret', 'group': 'embedding'},
    {'key': 'rag.embedding_batch_size', 'label': 'Số đoạn mỗi lần gọi', 'type': 'int', 'group': 'embedding', 'min': 1, 'max': 512},
    {'key': 'rag.chunk_size', 'label': 'Độ dài đoạn (ký tự)', 'type': 'int', 'group': 'embedding', 'min': 100, 'max': 20000},
    {'key': 'rag.chunk_overlap', 'label': 'Chồng lấn (ký tự)', 'type': 'int', 'group': 'embedding', 'min': 0, 'max': 5000},
    {'key': 'rag.top_k', 'label': 'Số đoạn lấy ra (Top K)', 'type': 'int', 'group': 'embedding', 'min': 1, 'max': 100},
    {'key': 'rag.enable_hybrid_search', 'label': 'Tìm kiếm kết hợp (ngữ nghĩa + từ khóa)', 'type': 'bool', 'group': 'embedding'},
    # Reranker
    {'key': 'rag.reranking_engine', 'label': 'Engine reranker', 'type': 'select', 'group': 'reranker',
     'options': [['', 'Local (CrossEncoder, nặng RAM)'], ['external', 'API ngoài (vLLM /v1/rerank, Jina, Cohere)']]},
    {'key': 'rag.reranking_model', 'label': 'Model reranker', 'type': 'text', 'group': 'reranker', 'help': 'Trống = không dùng reranker'},
    {'key': 'rag.external_reranker_url', 'label': 'URL reranker', 'type': 'text', 'group': 'reranker', 'help': 'URL đầy đủ, vd https://<pod>-8000.proxy.runpod.net/v1/rerank'},
    {'key': 'rag.external_reranker_api_key', 'label': 'Khóa API reranker', 'type': 'secret', 'group': 'reranker'},
    {'key': 'rag.top_k_reranker', 'label': 'Số đoạn giữ lại sau rerank', 'type': 'int', 'group': 'reranker', 'min': 1, 'max': 100},
    # Document extraction
    {'key': 'rag.content_extraction_engine', 'label': 'Công cụ trích xuất', 'type': 'select', 'group': 'extraction',
     'options': [['', 'Có sẵn (pypdf, python-docx, unstructured)'], ['tika', 'Apache Tika'], ['docling', 'Docling'], ['mistral_ocr', 'Mistral OCR'], ['external', 'Dịch vụ ngoài']]},
    {'key': 'rag.tika_server_url', 'label': 'URL Tika', 'type': 'text', 'group': 'extraction'},
    {'key': 'rag.docling_server_url', 'label': 'URL Docling', 'type': 'text', 'group': 'extraction'},
    {'key': 'rag.pdf_extract_images', 'label': 'Đọc chữ trong ảnh của PDF', 'type': 'bool', 'group': 'extraction'},
    # Speech to text
    {'key': 'audio.stt.engine', 'label': 'Engine nhận dạng giọng nói', 'type': 'select', 'group': 'stt',
     'options': [['', 'Local (faster-whisper)'], ['openai', 'API dạng OpenAI'], ['web', 'Trình duyệt'], ['deepgram', 'Deepgram'], ['azure', 'Azure']]},
    {'key': 'audio.stt.whisper_model', 'label': 'Model whisper (local)', 'type': 'text', 'group': 'stt', 'help': 'vd base, small, medium'},
    {'key': 'audio.stt.model', 'label': 'Model STT (API)', 'type': 'text', 'group': 'stt'},
    {'key': 'audio.stt.openai.api_base_url', 'label': 'URL API STT', 'type': 'text', 'group': 'stt'},
    {'key': 'audio.stt.openai.api_key', 'label': 'Khóa API STT', 'type': 'secret', 'group': 'stt'},
    # Text to speech
    {'key': 'audio.tts.engine', 'label': 'Engine đọc văn bản', 'type': 'select', 'group': 'tts',
     'options': [['', 'Trình duyệt'], ['openai', 'API dạng OpenAI'], ['elevenlabs', 'ElevenLabs'], ['azure', 'Azure']]},
    {'key': 'audio.tts.model', 'label': 'Model TTS', 'type': 'text', 'group': 'tts'},
    {'key': 'audio.tts.voice', 'label': 'Giọng', 'type': 'text', 'group': 'tts'},
    {'key': 'audio.tts.openai.api_base_url', 'label': 'URL API TTS', 'type': 'text', 'group': 'tts'},
    {'key': 'audio.tts.openai.api_key', 'label': 'Khóa API TTS', 'type': 'secret', 'group': 'tts'},
    # Web search
    {'key': 'web.search.enable', 'label': 'Bật tìm kiếm web', 'type': 'bool', 'group': 'web'},
    {'key': 'web.search.engine', 'label': 'Công cụ tìm kiếm', 'type': 'text', 'group': 'web', 'help': 'vd duckduckgo, searxng, tavily, google_pse (khóa của từng công cụ đặt trong NCS AI)'},
    # Images
    {'key': 'image_generation.enable', 'label': 'Bật tạo ảnh', 'type': 'bool', 'group': 'image'},
    {'key': 'image_generation.engine', 'label': 'Engine tạo ảnh', 'type': 'select', 'group': 'image',
     'options': [['openai', 'API dạng OpenAI'], ['comfyui', 'ComfyUI'], ['automatic1111', 'Automatic1111'], ['gemini', 'Gemini']]},
    {'key': 'image_generation.model', 'label': 'Model tạo ảnh', 'type': 'text', 'group': 'image'},
    {'key': 'image_generation.openai.api_base_url', 'label': 'URL API tạo ảnh', 'type': 'text', 'group': 'image'},
    {'key': 'image_generation.openai.api_key', 'label': 'Khóa API tạo ảnh', 'type': 'secret', 'group': 'image'},
    # Code interpreter
    {'key': 'code_interpreter.enable', 'label': 'Bật chạy code', 'type': 'bool', 'group': 'code'},
    {'key': 'code_interpreter.engine', 'label': 'Engine chạy code', 'type': 'select', 'group': 'code',
     'options': [['pyodide', 'Pyodide (trong trình duyệt)'], ['jupyter', 'Jupyter']]},
]  # fmt: skip
GROUPS = [
    ('tracker', 'Tracker tiến độ', 'Áp dụng trong tối đa 15 giây, không cần khởi động lại.'),
    ('task', 'Model tác vụ nền', ''),
    (
        'embedding',
        'Kiến thức: embedding',
        'Đổi engine/model xong phải bấm "Lập chỉ mục lại" trong Cài đặt quản trị → Tài liệu.',
    ),
    ('reranker', 'Kiến thức: reranker', 'Chỉ dùng khi bật Tìm kiếm kết hợp và tắt Bỏ qua embedding.'),
    ('extraction', 'Trích xuất tài liệu', ''),
    ('stt', 'Nhận dạng giọng nói (STT)', ''),
    ('tts', 'Đọc văn bản (TTS)', ''),
    ('web', 'Tìm kiếm web', ''),
    ('image', 'Tạo ảnh', ''),
    ('code', 'Chạy code', ''),
]
BY_KEY = {s['key']: s for s in SETTINGS}

_sessions: dict[str, tuple[float, str]] = {}  # token -> (expires_at, client)
_failures: dict[str, list[float]] = {}


def _client(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    return forwarded or (request.client.host if request.client else '?')


def _engine():
    from open_webui.monitoring.server.app import engine

    if engine is None:
        raise HTTPException(503, 'DATABASE_URL is not set')
    return engine


async def _get(conn, keys: list[str]) -> dict:
    result = await conn.execute(text('SELECT key, value FROM config WHERE key = ANY(:keys)'), {'keys': keys})
    values = {}
    for key, value in result:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        values[key] = value
    return values


async def _put(conn, key: str, value: Any) -> None:
    await conn.execute(
        text(
            """INSERT INTO config (key, value, updated_at) VALUES (:key, CAST(:value AS json), :now)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"""
        ),
        {'key': key, 'value': json.dumps(value), 'now': int(time.time())},
    )


def require_session(request: Request) -> str:
    token = request.headers.get('x-edit-token', '')
    session = _sessions.get(token)
    if not session or session[0] < time.time() or session[1] != _client(request):
        _sessions.pop(token, None)
        raise HTTPException(
            401, 'Phiên chỉnh sửa đã hết hạn. Xác thực lại bằng khóa truy xuất và mã Google Authenticator.'
        )
    return token


@router.get('/api/admin/status')
async def admin_status(request: Request):
    async with _engine().connect() as conn:
        totp = (await _get(conn, [security.TOTP_KEY])).get(security.TOTP_KEY)
    token = request.headers.get('x-edit-token', '')
    session = _sessions.get(token)
    return {
        'admin_key_set': bool(security.ADMIN_KEY),
        'totp_set': bool(totp),
        'session_expires': int(session[0]) if session and session[0] > time.time() else None,
    }


class UnlockForm(BaseModel):
    admin_key: str
    code: str


@router.post('/api/admin/unlock')
async def unlock(form: UnlockForm, request: Request):
    if not security.ADMIN_KEY:
        raise HTTPException(503, 'Chưa đặt MONITOR_REVEAL_KEY nên không thể sửa cấu hình.')
    client = _client(request)
    now = time.time()
    recent = [t for t in _failures.get(client, []) if now - t < LOCK_SECONDS]
    _failures[client] = recent
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(
            429, f'Sai quá {MAX_FAILURES} lần. Thử lại sau {max(int(LOCK_SECONDS - (now - recent[0])) // 60, 1)} phút.'
        )

    def fail(message: str):
        recent.append(now)
        log.warning('Monitor admin: failed unlock from %s (%s)', client, message)
        return HTTPException(403, f'{message} (còn {MAX_FAILURES - len(recent)} lần thử).')

    if not security.check_admin_key(form.admin_key):
        raise fail('Khóa hoặc mã xác thực không đúng')
    async with _engine().begin() as conn:
        stored = (await _get(conn, [security.TOTP_KEY])).get(security.TOTP_KEY)
        if not stored:
            raise HTTPException(
                409,
                'Chưa cài mã Google Authenticator. Trên server chạy: '
                'docker compose exec monitor python -m open_webui.monitoring.server.totp init',
            )
        secret = security.decrypt_secret(stored.get('enc', ''))
        if not secret:
            raise HTTPException(
                409, 'Không giải mã được mã xác thực (MONITOR_REVEAL_KEY đã đổi?). Chạy totp reset rồi init.'
            )
        step = security.verify_code(secret, form.code, int(stored.get('last_step', -1)))
        if step is None:
            raise fail('Khóa hoặc mã xác thực không đúng')
        await _put(conn, security.TOTP_KEY, {**stored, 'last_step': step})
    _failures.pop(client, None)
    for token, (expires, _) in list(_sessions.items()):
        if expires < now:
            _sessions.pop(token, None)
    token = secrets.token_urlsafe(32)
    _sessions[token] = (now + SESSION_SECONDS, client)
    log.info('Monitor admin: edit session opened for %s', client)
    return {'token': token, 'expires': int(now + SESSION_SECONDS)}


@router.post('/api/admin/lock')
async def lock(request: Request):
    _sessions.pop(request.headers.get('x-edit-token', ''), None)
    return {'ok': True}


def _shown(setting: dict, value: Any) -> Any:
    if setting['type'] == 'secret':
        return {'set': bool(value)}
    return value


@router.get('/api/admin/settings')
async def get_settings(token: str = Depends(require_session)):
    async with _engine().connect() as conn:
        values = await _get(conn, list(BY_KEY))
        history = await conn.execute(
            text(
                """SELECT ts, name, meta FROM perf_event WHERE kind = 'config_change'
                   ORDER BY ts DESC LIMIT 30"""
            )
        )
        history = [dict(r._mapping) for r in history]
    return {
        'groups': [{'key': g, 'label': label, 'note': note} for g, label, note in GROUPS],
        'settings': [{**s, 'value': _shown(s, values.get(s['key']))} for s in SETTINGS],
        'history': history,
        'expires': int(_sessions[token][0]),
    }


def _coerce(setting: dict, value: Any) -> Any:
    kind = setting['type']
    if kind == 'bool':
        return value if isinstance(value, bool) else str(value).lower() in ('1', 'true', 'on', 'yes')
    if kind == 'int':
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f'{setting["label"]}: phải là số nguyên')
        low, high = setting.get('min'), setting.get('max')
        if (low is not None and number < low) or (high is not None and number > high):
            raise HTTPException(400, f'{setting["label"]}: phải trong khoảng {low}–{high}')
        return number
    value = '' if value is None else str(value).strip()
    if kind == 'select' and value not in [o[0] for o in setting.get('options', [])]:
        raise HTTPException(400, f'{setting["label"]}: giá trị không hợp lệ')
    if len(value) > 2000:
        raise HTTPException(400, f'{setting["label"]}: quá dài')
    return value


class SaveForm(BaseModel):
    changes: dict[str, Any]


@router.post('/api/admin/settings')
async def save_settings(form: SaveForm, request: Request, token: str = Depends(require_session)):
    unknown = [k for k in form.changes if k not in BY_KEY]
    if unknown:
        raise HTTPException(400, f'Không được sửa: {", ".join(unknown)}')
    changes = {}
    for key, value in form.changes.items():
        setting = BY_KEY[key]
        if setting['type'] == 'secret' and (value is None or value == ''):
            continue  # empty secret field = keep the current one
        changes[key] = _coerce(setting, value)
    if not changes:
        return {'changed': []}
    client = _client(request)
    async with _engine().begin() as conn:
        current = await _get(conn, list(changes) + [VERSION_KEY])
        changed = [k for k, v in changes.items() if current.get(k) != v]
        if not changed:
            return {'changed': []}
        groups = sorted({BY_KEY[k]['group'] for k in changed})
        now_ms = int(time.time() * 1000)
        for key in changed:
            await _put(conn, key, changes[key])
            secret = BY_KEY[key]['type'] == 'secret'
            await conn.execute(
                text(
                    """INSERT INTO perf_event (id, ts, kind, source, name, status, meta)
                       VALUES (:id, :ts, 'config_change', 'monitor', :name, 'ok', CAST(:meta AS json))"""
                ),
                {
                    'id': uuid.uuid4().hex,
                    'ts': now_ms,
                    'name': key,
                    'meta': json.dumps(
                        {
                            'label': BY_KEY[key]['label'],
                            'group': BY_KEY[key]['group'],
                            'old': '(khóa bí mật)' if secret else current.get(key),
                            'new': '(đã đổi)' if secret else changes[key],
                            'client': client,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
        version = current.get(VERSION_KEY) if isinstance(current.get(VERSION_KEY), dict) else {}
        await _put(
            conn,
            VERSION_KEY,
            {'version': int(version.get('version') or 0) + 1, 'groups': groups, 'at': int(time.time())},
        )
    log.info('Monitor admin: %s changed %s', client, ', '.join(changed))
    return {'changed': changed, 'groups': groups}


# ---------------------------------------------------------------------------------------------------------
# Model connections (OpenAI-compatible: RunPod/vLLM, OpenAI...). URL, API key, on/off and prefix of each
# connection; the other per-connection settings made in NCS AI (model list, tags, API type) are kept.
# ---------------------------------------------------------------------------------------------------------
CONNECTION_KEYS = ['openai.api_base_urls', 'openai.api_keys', 'openai.api_configs']


def _mask_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url or '')
        host = (parts.hostname or '') + (f':{parts.port}' if parts.port else '')
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except Exception:
        return ''


async def _connections(conn) -> tuple[list[str], list[str], dict]:
    values = await _get(conn, CONNECTION_KEYS)
    urls = list(values.get('openai.api_base_urls') or [])
    keys = list(values.get('openai.api_keys') or [])
    keys += [''] * (len(urls) - len(keys))
    configs = values.get('openai.api_configs') or {}
    return urls, keys[: len(urls)], configs if isinstance(configs, dict) else {}


def _config_of(configs: dict, index: int, url: str) -> dict:
    return dict(configs.get(str(index), configs.get(url, {})) or {})


@router.get('/api/admin/connections')
async def get_connections(token: str = Depends(require_session)):
    async with _engine().connect() as conn:
        urls, keys, configs = await _connections(conn)
    return {
        'connections': [
            {
                'index': i,
                'url': url,
                'key_set': bool(keys[i]),
                'enable': _config_of(configs, i, url).get('enable', True),
                'prefix_id': _config_of(configs, i, url).get('prefix_id') or '',
            }
            for i, url in enumerate(urls)
        ]
    }


class ConnectionIn(BaseModel):
    index: Optional[int] = None  # existing connection (its key/config are kept when left empty); None = new
    url: str
    key: str = ''
    enable: bool = True
    prefix_id: str = ''


class ConnectionsForm(BaseModel):
    connections: list[ConnectionIn]


def _check_url(url: str) -> str:
    url = (url or '').strip().rstrip('/')
    if not url.startswith(('http://', 'https://')) or len(url) > 500 or ' ' in url:
        raise HTTPException(400, f'URL không hợp lệ: {url or "(trống)"}. URL phải bắt đầu bằng http:// hoặc https://')
    return url


async def _audit(conn, label: str, old_value, new_value, client: str, name: str = 'openai.connections') -> None:
    await conn.execute(
        text(
            """INSERT INTO perf_event (id, ts, kind, source, name, status, meta)
               VALUES (:id, :ts, 'config_change', 'monitor', :name, 'ok', CAST(:meta AS json))"""
        ),
        {
            'id': uuid.uuid4().hex,
            'ts': int(time.time() * 1000),
            'name': name,
            'meta': json.dumps(
                {'label': label, 'group': 'connections', 'old': old_value, 'new': new_value, 'client': client},
                ensure_ascii=False,
            ),
        },
    )


@router.post('/api/admin/connections')
async def save_connections(form: ConnectionsForm, request: Request, token: str = Depends(require_session)):
    if not form.connections:
        raise HTTPException(400, 'Phải còn ít nhất một kết nối.')
    client = _client(request)
    async with _engine().begin() as conn:
        urls, keys, configs = await _connections(conn)
        new_urls, new_keys, new_configs, changes = [], [], {}, []
        for position, item in enumerate(form.connections):
            url = _check_url(item.url)
            old = item.index if item.index is not None and 0 <= item.index < len(urls) else None
            key = item.key.strip() or (keys[old] if old is not None else '')
            old_config = _config_of(configs, old, urls[old]) if old is not None else {}
            config = dict(old_config)
            config['enable'] = bool(item.enable)
            if item.prefix_id.strip():
                config['prefix_id'] = item.prefix_id.strip()
            else:
                config.pop('prefix_id', None)
            new_urls.append(url)
            new_keys.append(key)
            new_configs[str(position)] = config
            name = f'Kết nối #{position + 1}'
            if old is None:
                changes.append((name, None, f'thêm {_mask_url(url)}'))
                continue
            if urls[old].rstrip('/') != url:
                changes.append((f'{name}: URL', _mask_url(urls[old]), _mask_url(url)))
            if item.key.strip() and item.key.strip() != keys[old]:
                changes.append((f'{name}: khóa API', '(khóa bí mật)', '(đã đổi)'))
            if old_config.get('enable', True) != config['enable']:
                changes.append((f'{name}: bật', old_config.get('enable', True), config['enable']))
            if (old_config.get('prefix_id') or '') != (config.get('prefix_id') or ''):
                changes.append((f'{name}: prefix', old_config.get('prefix_id'), config.get('prefix_id')))
            if old != position:
                changes.append((f'{name}: thứ tự', f'#{old + 1}', f'#{position + 1}'))
        kept = {i.index for i in form.connections if i.index is not None}
        for i, url in enumerate(urls):
            if i not in kept:
                changes.append((f'Kết nối cũ #{i + 1}', _mask_url(url), 'đã xóa'))
        if not changes:
            return {'changed': []}
        await _put(conn, 'openai.api_base_urls', new_urls)
        await _put(conn, 'openai.api_keys', new_keys)
        await _put(conn, 'openai.api_configs', new_configs)
        for label, old_value, new_value in changes:
            await _audit(conn, label, old_value, new_value, client)
        version = (await _get(conn, [VERSION_KEY])).get(VERSION_KEY)
        version = version if isinstance(version, dict) else {}
        await _put(
            conn,
            VERSION_KEY,
            {'version': int(version.get('version') or 0) + 1, 'groups': ['connections'], 'at': int(time.time())},
        )
    log.info('Monitor admin: %s changed model connections (%d changes)', client, len(changes))
    return {'changed': [c[0] for c in changes]}


class TestForm(BaseModel):
    url: str
    key: str = ''
    index: Optional[int] = None  # use the saved key of this connection when key is empty


@router.post('/api/admin/connections/test')
async def test_connection(form: TestForm, token: str = Depends(require_session)):
    """GET <url>/models with the key, so a new URL/key can be checked before saving."""
    import aiohttp

    url = _check_url(form.url)
    key = form.key.strip()
    if not key and form.index is not None:
        async with _engine().connect() as conn:
            _, keys, _ = await _connections(conn)
        key = keys[form.index] if 0 <= form.index < len(keys) else ''
    started = time.perf_counter()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            headers = {'Authorization': f'Bearer {key}'} if key else {}
            async with session.get(f'{url}/models', headers=headers) as response:
                status = response.status
                body = await response.json(content_type=None) if status == 200 else await response.text()
    except Exception as e:
        return {'ok': False, 'error': f'Không kết nối được: {type(e).__name__}: {e}'[:300]}
    ms = round((time.perf_counter() - started) * 1000)
    if status != 200:
        return {'ok': False, 'ms': ms, 'error': f'HTTP {status}: {str(body)[:200]}'}
    data = body.get('data') if isinstance(body, dict) else []
    models = [m.get('id') for m in data or [] if isinstance(m, dict)]
    return {'ok': True, 'ms': ms, 'models': models[:30]}


# ---------------------------------------------------------------------------------------------------------
# Rebuild knowledge and memory vectors (after changing the embedding model)
# ---------------------------------------------------------------------------------------------------------
REINDEX_KEY = 'monitor.reindex'


async def _reindex_status(conn) -> dict:
    state = (await _get(conn, [REINDEX_KEY])).get(REINDEX_KEY) or {}
    state = state if isinstance(state, dict) else {}
    total = (await conn.execute(text('SELECT count(*) FROM knowledge_file'))).scalar() or 0
    done = 0
    if state.get('started_at'):
        done = (
            await conn.execute(
                text("SELECT count(*) FROM perf_event WHERE kind = 'ingest' AND source = 'knowledge' AND ts >= :t"),
                {'t': int(state['started_at']) * 1000},
            )
        ).scalar() or 0
    return {**state, 'files_total': total, 'files_done': done}


@router.get('/api/admin/reindex')
async def reindex_status(token: str = Depends(require_session)):
    async with _engine().connect() as conn:
        return await _reindex_status(conn)


@router.post('/api/admin/reindex')
async def reindex_start(request: Request, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui('/reindex', method='POST', timeout=30)
    if result.get('error'):
        raise HTTPException(502, result['error'])
    if not result.get('already_running'):
        async with _engine().begin() as conn:
            await _audit(
                conn, 'Lập chỉ mục lại vector kiến thức và bộ nhớ', None, 'bắt đầu', _client(request), 'reindex'
            )
    async with _engine().connect() as conn:
        return {**(await _reindex_status(conn)), 'already_running': bool(result.get('already_running'))}


# ---------------------------------------------------------------------------------------------------------
# Model access: public (every user) or private (admins + users/groups it is shared with in NCS AI)
# ---------------------------------------------------------------------------------------------------------
DEFAULT_ACCESS_KEY = 'models.default_access'
DEFAULT_ACCESS_OPTIONS = {
    '': 'Không: model mới chỉ admin thấy',
    'public': 'Model mới của kết nối #1 tự công khai',
    'public_all': 'Model mới của mọi kết nối tự công khai',
}


async def _bump(conn, groups: list[str]) -> None:
    version = (await _get(conn, [VERSION_KEY])).get(VERSION_KEY)
    version = version if isinstance(version, dict) else {}
    await _put(
        conn, VERSION_KEY, {'version': int(version.get('version') or 0) + 1, 'groups': groups, 'at': int(time.time())}
    )


@router.get('/api/admin/models')
async def models_access(token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui('/models', timeout=60)
    if result.get('error'):
        raise HTTPException(502, result['error'])
    return {**result, 'default_options': [[k, v] for k, v in DEFAULT_ACCESS_OPTIONS.items()]}


class ModelAccessForm(BaseModel):
    model_id: str
    public: bool
    name: str = ''


@router.post('/api/admin/models/access')
async def set_model_access(form: ModelAccessForm, request: Request, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui('/models/access', method='POST', json_body=form.model_dump())
    if result.get('error'):
        raise HTTPException(502, result['error'])
    async with _engine().begin() as conn:
        await _audit(
            conn,
            f'Quyền model {form.model_id}',
            'riêng tư' if form.public else 'công khai',
            'công khai' if form.public else 'riêng tư',
            _client(request),
            'model.access',
        )
        await _bump(conn, ['models'])
    return result


class DefaultAccessForm(BaseModel):
    value: str


@router.post('/api/admin/models/default')
async def set_default_access(form: DefaultAccessForm, request: Request, token: str = Depends(require_session)):
    if form.value not in DEFAULT_ACCESS_OPTIONS:
        raise HTTPException(400, 'Giá trị không hợp lệ')
    async with _engine().begin() as conn:
        old = (await _get(conn, [DEFAULT_ACCESS_KEY])).get(DEFAULT_ACCESS_KEY)
        await _put(conn, DEFAULT_ACCESS_KEY, form.value)
        await _audit(
            conn,
            'Quyền mặc định của model mới',
            DEFAULT_ACCESS_OPTIONS.get(old, '(theo .env)') if old is not None else '(theo .env)',
            DEFAULT_ACCESS_OPTIONS[form.value],
            _client(request),
            DEFAULT_ACCESS_KEY,
        )
        await _bump(conn, ['models'])
    return {'value': form.value}
