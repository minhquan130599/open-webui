"""Forwarding Open WebUI's terminal requests (HTTP and websockets) to the caller's own terminal container.

Who the caller is comes from X-User-Id / X-User-Role, set by the Open WebUI backend from the signed-in user (the
browser never talks to the broker). Requests without a user may only read the tool list and the system prompt, served
by a shared terminal holding no data.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from contextlib import suppress
from typing import Optional
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse

from broker.config import Settings
from broker.pool import SPEC, Busy, Endpoint, Pool, StartFailed

log = logging.getLogger('broker.proxy')

HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers',
              'transfer-encoding', 'upgrade', 'host', 'content-length', 'authorization'}
# What Open WebUI asks without a user: the tool list (openapi.json) and the terminal's system prompt.
ANONYMOUS_PATHS = re.compile(r'^/(openapi\.json|api/config|system|health)/?$')
# Commands still allowed over the disk quota, to free space.
CLEANUP_COMMAND = re.compile(r'^\s*(rm|du|ls|df|find\s.*-delete|truncate)\b')
OOM_EXIT_CODES = {137, -9}
OOM_NOTE = ('Tiến trình bị dừng vì vượt giới hạn bộ nhớ {mb} MB của terminal. Hãy xử lý dữ liệu theo từng phần nhỏ hơn '
            '(đọc theo khối, ít cột hơn, ít trang hơn mỗi lần). / The process was killed: it went over the terminal '
            'memory limit of {mb} MB. Process the data in smaller parts.')
BUSY_MESSAGE = ('Tất cả terminal đang được sử dụng, hãy thử lại sau ít phút. / All terminals are in use, try again in '
                'a few minutes.')
QUOTA_MESSAGE = ('Thư mục của bạn trong terminal đã dùng quá {mb} MB. Hãy xóa bớt file (lệnh rm, du, ls vẫn chạy được) '
                 'rồi thử lại. / Your terminal folder is over {mb} MB: delete files (rm, du, ls still work).')


def authorized(settings: Settings, value: str) -> bool:
    token = value[7:] if value.lower().startswith('bearer ') else value
    return bool(settings.api_key) and hmac.compare_digest(token.strip(), settings.api_key)


def identity(headers, query=None) -> tuple[Optional[str], str]:
    user_id = headers.get('x-user-id') or (query or {}).get('user_id') or None
    if user_id == 'system':
        user_id = None
    role = (headers.get('x-user-role') or 'user').lower()
    return user_id, ('admin' if role == 'admin' else 'user')


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({'error': message, 'detail': message}, status_code=status)


class Proxy:
    def __init__(self, settings: Settings, pool: Pool, transport: Optional[httpx.AsyncBaseTransport] = None,
                 ws_connect=None):
        self.s = settings
        self.pool = pool
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), transport=transport)
        self.ws_connect = ws_connect or websockets.connect

    async def close(self) -> None:
        await self.client.aclose()

    async def _endpoint(self, user_id: Optional[str], role: str) -> Endpoint:
        return await self.pool.endpoint(user_id or SPEC, role)

    # ------------------------------------------------------------ HTTP

    async def http(self, request: Request, path: str) -> Response:
        if not authorized(self.s, request.headers.get('authorization', '')):
            return _error(401, 'Invalid terminal broker key')
        user_id, role = identity(request.headers)
        if not user_id and not (request.method == 'GET' and ANONYMOUS_PATHS.match('/' + path)):
            return _error(403, 'A user is required for this terminal request')
        body = await request.body()
        try:
            endpoint = await self._endpoint(user_id, role)
        except Busy:
            return _error(503, BUSY_MESSAGE)
        except StartFailed as error:
            return _error(503, f'Terminal could not start: {error}')
        if endpoint.over_quota and not self._cleanup(request.method, path, body):
            self.pool.event('quota_refused', endpoint.user_hash, path)
            return _error(507, QUOTA_MESSAGE.format(mb=self.s.disk_quota_mb))
        for attempt in (1, 2):
            try:
                return await self._forward(request, path, body, endpoint)
            except (httpx.ConnectError, httpx.RemoteProtocolError) as error:
                if attempt == 2:
                    return _error(502, f'Terminal unreachable: {error}')
                self.pool.mark_dead(endpoint.user_hash)
                try:
                    endpoint = await self._endpoint(user_id, role)
                except Busy:
                    return _error(503, BUSY_MESSAGE)
                except StartFailed as start_error:
                    return _error(503, f'Terminal could not start: {start_error}')
        return _error(502, 'Terminal unreachable')

    @staticmethod
    def _cleanup(method: str, path: str, body: bytes) -> bool:
        if method in ('GET', 'HEAD', 'OPTIONS', 'DELETE') or 'delete' in path.lower():
            return True
        try:
            command = json.loads(body or b'{}').get('command', '')
        except (ValueError, AttributeError):
            return False
        return bool(isinstance(command, str) and CLEANUP_COMMAND.match(command))

    async def _forward(self, request: Request, path: str, body: bytes, endpoint: Endpoint) -> Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        headers['Authorization'] = f'Bearer {endpoint.api_key}'
        url = f'{endpoint.url}/{path}'
        if request.url.query:
            url += f'?{request.url.query}'
        h = endpoint.user_hash
        usage = self.pool.using(h)
        await usage.__aenter__()
        try:
            upstream = await self.client.send(
                self.client.build_request(request.method, url, headers=headers, content=body), stream=True)
        except BaseException:
            await usage.__aexit__(None, None, None)
            raise
        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
        kind = upstream.headers.get('content-type', '')
        length = int(upstream.headers.get('content-length') or 0)
        if 'json' in kind and length <= 2_000_000 and 'stream' not in kind:
            try:
                content = await upstream.aread()
            finally:
                await upstream.aclose()
                await usage.__aexit__(None, None, None)
            content = self._note_oom(content, h)
            out_headers.pop('content-encoding', None)
            return Response(content, status_code=upstream.status_code, headers=out_headers, media_type=None)

        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await usage.__aexit__(None, None, None)

        return StreamingResponse(stream(), status_code=upstream.status_code, headers=out_headers)

    def _note_oom(self, content: bytes, h: str) -> bytes:
        """A process killed for memory reads as exit code 137: say so in words the model acts on."""
        if b'137' not in content and b'-9' not in content:
            return content
        try:
            data = json.loads(content)
        except ValueError:
            return content
        if isinstance(data, dict) and data.get('exit_code') in OOM_EXIT_CODES:
            data['note'] = OOM_NOTE.format(mb=self.s.memory_mb)
            self.pool.event('oom', h, str(data.get('command', ''))[:200])
            return json.dumps(data, ensure_ascii=False).encode('utf-8')
        return content

    # ------------------------------------------------------------ websockets

    async def websocket(self, ws: WebSocket, path: str) -> None:
        await ws.accept()
        user_id, role = identity(ws.headers, dict(ws.query_params))
        if not user_id:
            await ws.close(code=4003, reason='A user is required')
            return
        # Two ways Open WebUI authenticates: the Authorization header (browser view relay), or a first message
        # {"type": "auth", "token": ...} (terminal sessions, events, execute streams).
        header_auth = authorized(self.s, ws.headers.get('authorization', ''))
        auth = None
        if not header_auth:
            try:
                first = await asyncio.wait_for(ws.receive_text(), timeout=15)
                auth = json.loads(first)
            except Exception:
                await ws.close(code=4001, reason='Authentication message expected')
                return
            if not isinstance(auth, dict) or not authorized(self.s, str(auth.get('token') or '')):
                await ws.close(code=4001, reason='Invalid terminal broker key')
                return
        try:
            endpoint = await self._endpoint(user_id, role)
        except Busy:
            await ws.close(code=4008, reason=BUSY_MESSAGE[:120])
            return
        except StartFailed:
            await ws.close(code=1011, reason='Terminal could not start')
            return
        query = {k: v for k, v in ws.query_params.items()}
        target = endpoint.url.replace('http://', 'ws://', 1) + f'/{path}'
        if query:
            target += f'?{urlencode(query)}'
        headers = {'X-User-Id': user_id}
        if ws.headers.get('x-session-id'):
            headers['X-Session-Id'] = ws.headers['x-session-id']
        if header_auth:
            headers['Authorization'] = f'Bearer {endpoint.api_key}'
        async with self.pool.using(endpoint.user_hash):
            try:
                async with self.ws_connect(target, additional_headers=headers, max_size=None,
                                           open_timeout=15) as upstream:
                    if auth is not None:
                        await upstream.send(json.dumps({**auth, 'token': endpoint.api_key}))
                    await self._pump(ws, upstream, endpoint.user_hash)
            except Exception as error:
                log.info('terminal websocket %s ended: %s', path, error)
                with suppress(Exception):
                    await ws.close(code=1011, reason='Terminal connection interrupted')

    async def _pump(self, ws: WebSocket, upstream, h: str) -> None:
        async def client_to_upstream():
            while True:
                message = await ws.receive()
                if message['type'] == 'websocket.disconnect':
                    break
                if message.get('bytes'):
                    await upstream.send(message['bytes'])
                elif message.get('text'):
                    await upstream.send(message['text'])
                self.pool.touch(h)

        async def upstream_to_client():
            async for message in upstream:
                if isinstance(message, bytes):
                    await ws.send_bytes(message)
                else:
                    await ws.send_text(message)
                self.pool.touch(h)

        tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        with suppress(Exception):
            await upstream.close()
        with suppress(Exception):
            await ws.close()
