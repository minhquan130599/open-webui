"""Terminal broker without Docker: a fake Docker API, a fake clock and fake terminals.

Run: python -m pytest terminal-broker/tests -q   (with terminal-broker on PYTHONPATH)
"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from broker import egress
from broker.config import Settings
from broker.docker_api import DockerError
from broker.pool import LXCFS_FILES, SPEC, Busy, Pool, user_hash
from broker.proxy import Proxy
from broker.store import Store


class FakeDocker:
    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.volumes: set[str] = set()
        self.calls: list[tuple] = []
        self.du_kb = 1000
        self.next_ip = 10
        self.refuse_lxcfs = False

    async def create_volume(self, name, labels):
        self.volumes.add(name)

    async def remove_volume(self, name):
        self.volumes.discard(name)
        self.calls.append(('remove_volume', name))

    async def inspect(self, name):
        c = self.containers.get(name)
        if not c:
            return None
        return {'State': {'Running': c['running']},
                'Mounts': [{'Destination': m['Target']} for m in c['spec']['HostConfig']['Mounts']],
                'NetworkSettings': {'Networks': {'terminal-net': {'IPAddress': c['ip']}}}}

    async def create(self, name, spec):
        self.next_ip += 1
        self.containers[name] = {'running': False, 'spec': spec, 'ip': f'172.31.250.{self.next_ip}'}
        self.calls.append(('create', name))

    async def start(self, name):
        mounts = self.containers[name]['spec']['HostConfig']['Mounts']
        if self.refuse_lxcfs and any(m['Target'] == '/proc/meminfo' for m in mounts):
            raise DockerError(f'POST /containers/{name}/start: 500 error mounting "/var/lib/lxcfs/proc/meminfo"')
        self.containers[name]['running'] = True
        self.calls.append(('start', name))

    async def stop(self, name, timeout=10):
        if name in self.containers:
            self.containers[name]['running'] = False
        self.calls.append(('stop', name))

    async def remove(self, name):
        self.containers.pop(name, None)
        self.calls.append(('remove', name))

    async def list(self):
        return [{'Names': [f'/{n}'], 'State': 'running' if c['running'] else 'exited'} for n, c in self.containers.items()]

    async def exec(self, name, cmd, timeout=60):
        return f'{self.du_kb}\t/home/user\r\n'

    def running(self):
        return sorted(n for n, c in self.containers.items() if c['running'])


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


async def ready(url, key):
    return True


def make_pool(**overrides):
    s = Settings(api_key='broker-key', admin_key='admin-key', max_running=3, admin_reserved=1, evict_min_idle=120,
                 wait_seconds=10, idle_stop=900, stopped_remove=86400, volume_remove=7 * 86400, disk_quota_mb=5,
                 lxcfs_check_dir='/nonexistent-lxcfs')
    for k, v in overrides.items():
        setattr(s, k, v)
    docker, clock = FakeDocker(), Clock()
    pool = Pool(s, docker, Store(':memory:'), ready, clock=clock, sleep=clock.sleep)
    return pool, docker, clock


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- capacity


def test_each_user_gets_an_own_locked_down_container():
    async def go():
        pool, docker, _ = make_pool()
        a = await pool.endpoint('alice', 'user')
        b = await pool.endpoint('bob', 'user')
        again = await pool.endpoint('alice', 'user')
        assert a.url != b.url and a.api_key != b.api_key and again.url == a.url
        assert len(docker.running()) == 2
        spec = docker.containers[f'term-{user_hash("alice")}']['spec']
        host = spec['HostConfig']
        assert host['Memory'] == host['MemorySwap'] == 2048 * 1024 * 1024
        assert host['NanoCpus'] == 1_000_000_000 and host['PidsLimit'] == 256
        assert host['CapDrop'] == ['ALL'] and 'no-new-privileges:true' in host['SecurityOpt']
        assert host['ReadonlyRootfs'] is True and host['NetworkMode'] == 'terminal-net'
        assert host['Mounts'][0]['Source'] == f'term-home-{user_hash("alice")}'
        assert any(e.startswith('HTTPS_PROXY=') for e in spec['Env'])
        assert f'OPEN_TERMINAL_API_KEY={a.api_key}' in spec['Env']
    run(go())


def fake_lxcfs(tmp_path, files=LXCFS_FILES):
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('MemTotal:        2097152 kB\n')
    return str(tmp_path)


def lxcfs_targets(docker, user):
    mounts = docker.containers[f'term-{user_hash(user)}']['spec']['HostConfig']['Mounts']
    return {m['Target']: m for m in mounts if m['Type'] == 'bind'}


def test_lxcfs_files_are_mounted_when_the_host_runs_lxcfs(tmp_path):
    async def go():
        pool, docker, _ = make_pool(lxcfs_check_dir=fake_lxcfs(tmp_path))
        await pool.endpoint('alice', 'user')
        mounts = lxcfs_targets(docker, 'alice')
        assert set(mounts) == {f'/{rel}' for rel in LXCFS_FILES}
        assert mounts['/proc/meminfo'] == {'Type': 'bind', 'Source': '/var/lib/lxcfs/proc/meminfo',
                                           'Target': '/proc/meminfo', 'ReadOnly': True}
        env = docker.containers[f'term-{user_hash("alice")}']['spec']['Env']
        assert 'OMP_NUM_THREADS=1' in env and 'OPENBLAS_NUM_THREADS=1' in env
        assert pool.status()['lxcfs'] is True
    run(go())


def test_no_lxcfs_mounts_when_lxcfs_is_off_or_not_running(tmp_path):
    async def go():
        # lxcfs not running: its directory is empty (Docker made it for the broker's mount)
        pool, docker, _ = make_pool(lxcfs_check_dir=str(tmp_path))
        await pool.endpoint('alice', 'user')
        assert lxcfs_targets(docker, 'alice') == {} and pool.status()['lxcfs'] is False
        # switched off
        pool, docker, _ = make_pool(lxcfs_check_dir=fake_lxcfs(tmp_path), lxcfs='off')
        await pool.endpoint('alice', 'user')
        assert lxcfs_targets(docker, 'alice') == {}
        # an older lxcfs without some files: mount what it has
        other = tmp_path / 'old'
        pool, docker, _ = make_pool(lxcfs_check_dir=fake_lxcfs(other, ('proc/meminfo', 'proc/cpuinfo')))
        await pool.endpoint('alice', 'user')
        assert set(lxcfs_targets(docker, 'alice')) == {'/proc/meminfo', '/proc/cpuinfo'}
    run(go())


def test_a_terminal_starts_without_lxcfs_when_docker_refuses_it(tmp_path):
    async def go():
        pool, docker, _ = make_pool(lxcfs_check_dir=fake_lxcfs(tmp_path))
        docker.refuse_lxcfs = True
        await pool.endpoint('alice', 'user')
        assert docker.running() == [f'term-{user_hash("alice")}']
        assert lxcfs_targets(docker, 'alice') == {} and pool.lxcfs_failed
        await pool.endpoint('bob', 'user')  # later terminals do not try again
        assert lxcfs_targets(docker, 'bob') == {}
    run(go())


def test_a_stopped_terminal_made_with_lxcfs_is_made_again_when_lxcfs_is_gone(tmp_path):
    async def go():
        pool, docker, clock = make_pool(lxcfs_check_dir=fake_lxcfs(tmp_path))
        await pool.endpoint('alice', 'user')
        name = f'term-{user_hash("alice")}'
        clock.now += 901
        await pool.reap()
        assert name not in docker.running()
        # lxcfs stopped: the old container cannot start any more
        (tmp_path / 'proc' / 'meminfo').unlink()
        docker.refuse_lxcfs = True
        await pool.endpoint('alice', 'user')
        assert docker.running() == [name] and lxcfs_targets(docker, 'alice') == {}
        assert ('remove', name) in docker.calls and f'term-home-{user_hash("alice")}' in docker.volumes
    run(go())


def test_users_leave_one_place_for_admins():
    async def go():
        pool, docker, clock = make_pool()  # 3 places, 1 kept for admins -> 2 users
        await pool.endpoint('u1', 'user')
        await pool.endpoint('u2', 'user')
        async with pool.using(user_hash('u1')), pool.using(user_hash('u2')):
            with pytest.raises(Busy):
                await pool.endpoint('u3', 'user')  # waits, then busy
            admin = await pool.endpoint('boss', 'admin')  # the kept place
            assert admin
        assert len(docker.running()) == 3
    run(go())


def test_second_admin_takes_another_free_place():
    async def go():
        pool, docker, _ = make_pool()
        await pool.endpoint('a1', 'admin')
        await pool.endpoint('a2', 'admin')  # a free normal place
        assert len(docker.running()) == 2
        await pool.endpoint('u1', 'user')
        assert len(docker.running()) == 3
    run(go())


def test_the_longest_idle_terminal_makes_room():
    async def go():
        pool, docker, clock = make_pool()
        await pool.endpoint('u1', 'user')
        clock.now += 60
        await pool.endpoint('u2', 'user')
        clock.now += 300  # both idle more than 2 minutes, u1 the longest
        await pool.endpoint('u3', 'user')
        assert f'term-{user_hash("u1")}' not in docker.running()
        assert f'term-{user_hash("u2")}' in docker.running() and f'term-{user_hash("u3")}' in docker.running()
        assert pool.store.counts(0).get('evicted') == 1
        # u1 comes back: its container and files are reused (started again, not created again)
        clock.now += 300
        created_before = sum(1 for c in docker.calls if c[0] == 'create')
        await pool.endpoint('u1', 'user')
        assert sum(1 for c in docker.calls if c[0] == 'create') == created_before
    run(go())


def test_busy_terminals_are_never_taken():
    async def go():
        pool, docker, clock = make_pool(max_running=2, admin_reserved=0)
        await pool.endpoint('u1', 'user')
        await pool.endpoint('u2', 'user')
        clock.now += 600
        async with pool.using(user_hash('u1')):
            await pool.endpoint('u3', 'user')  # u2 idle -> taken, u1 busy -> kept
            assert f'term-{user_hash("u1")}' in docker.running()
            assert f'term-{user_hash("u2")}' not in docker.running()
    run(go())


def test_concurrent_first_requests_of_one_user_make_one_container():
    async def go():
        pool, docker, _ = make_pool()
        await asyncio.gather(*(pool.endpoint('alice', 'user') for _ in range(5)))
        assert sum(1 for c in docker.calls if c[0] == 'create') == 1
    run(go())


# ---------------------------------------------------------------- lifecycle and disk


def test_idle_stop_then_remove_then_expire():
    async def go():
        pool, docker, clock = make_pool()
        await pool.endpoint('alice', 'user')
        name, volume = f'term-{user_hash("alice")}', f'term-home-{user_hash("alice")}'
        clock.now += 14 * 60
        await pool.reap()
        assert name in docker.running()
        clock.now += 2 * 60  # 16 minutes idle
        await pool.reap()
        assert name not in docker.running() and name in docker.containers
        clock.now += 86400
        await pool.reap()
        assert name not in docker.containers and volume in docker.volumes  # files kept
        clock.now += 7 * 86400
        await pool.reap()
        assert volume not in docker.volumes and pool.store.get(user_hash('alice')) is None
    run(go())


def test_a_terminal_in_use_is_not_stopped():
    async def go():
        pool, docker, clock = make_pool()
        await pool.endpoint('alice', 'user')
        async with pool.using(user_hash('alice')):
            clock.now += 3600
            await pool.reap()
            assert f'term-{user_hash("alice")}' in docker.running()
    run(go())


def test_disk_quota_flag():
    async def go():
        pool, docker, _ = make_pool()  # quota 5 MB in this test
        await pool.endpoint('alice', 'user')
        docker.du_kb = 6 * 1024
        await pool.check_disk()
        assert (await pool.endpoint('alice', 'user')).over_quota
        docker.du_kb = 1024
        await pool.check_disk()
        assert not (await pool.endpoint('alice', 'user')).over_quota
    run(go())


def test_reconcile_removes_containers_without_a_key():
    async def go():
        pool, docker, _ = make_pool()
        docker.containers['term-stranger'] = {'running': True, 'spec': {}, 'ip': '1.1.1.1'}
        await pool.reconcile()
        assert 'term-stranger' not in docker.containers
    run(go())


# ---------------------------------------------------------------- proxy


class ChunkStream(httpx.AsyncByteStream):
    """A response body that arrives in pieces, like a real terminal download."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def make_app(pool):
    upstream_calls = []

    def handler(request: httpx.Request):
        upstream_calls.append(request)
        if request.url.path == '/execute':
            body = json.loads(request.content or b'{}')
            if body.get('command') == 'boom':
                return httpx.Response(200, json={'command': 'boom', 'exit_code': 137, 'output': []})
            return httpx.Response(200, json={'command': body.get('command'), 'exit_code': 0})
        if request.url.path == '/files/download':
            return httpx.Response(200, stream=ChunkStream([b'x' * 4, b'x' * 6]),
                                  headers={'content-type': 'application/octet-stream'})
        return httpx.Response(200, json={'path': request.url.path, 'host': request.url.host})

    proxy = Proxy(pool.s, pool, transport=httpx.MockTransport(handler))
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def fwd(path: str, request: Request):
        return await proxy.http(request, path)

    return app, upstream_calls


def headers(user=None, role='user', key='broker-key'):
    h = {'Authorization': f'Bearer {key}'}
    if user:
        h.update({'X-User-Id': user, 'X-User-Role': role})
    return h


def test_proxy_routes_each_user_to_their_own_terminal_with_its_own_key():
    pool, docker, _ = make_pool()
    app, calls = make_app(pool)
    with TestClient(app) as client:
        a = client.get('/files/list', headers=headers('alice')).json()
        b = client.get('/files/list', headers=headers('bob')).json()
    assert a['host'] == f'term-{user_hash("alice")}' and b['host'] == f'term-{user_hash("bob")}'
    keys = {c.headers['authorization'] for c in calls}
    assert len(keys) == 2 and 'Bearer broker-key' not in keys


def test_proxy_refuses_bad_key_and_missing_user():
    pool, _, _ = make_pool()
    app, calls = make_app(pool)
    with TestClient(app) as client:
        assert client.get('/files/list', headers=headers('alice', key='wrong')).status_code == 401
        assert client.post('/execute', headers=headers(None), json={'command': 'ls'}).status_code == 403
        spec = client.get('/openapi.json', headers=headers(None))  # tool list: shared empty terminal
        assert spec.status_code == 200 and spec.json()['host'] == f'term-{SPEC}'
    assert all(c.url.host != 'term-alice' for c in calls)


def test_proxy_explains_memory_kills():
    pool, _, _ = make_pool()
    app, _ = make_app(pool)
    with TestClient(app) as client:
        r = client.post('/execute', headers=headers('alice'), json={'command': 'boom'}).json()
    assert r['exit_code'] == 137 and '2048 MB' in r['note']
    assert pool.store.counts(0).get('oom') == 1


def test_proxy_over_quota_allows_only_cleanup():
    async def flag():
        await pool.endpoint('alice', 'user')
        docker.du_kb = 10 * 1024
        await pool.check_disk()

    pool, docker, _ = make_pool()
    run(flag())
    app, _ = make_app(pool)
    with TestClient(app) as client:
        assert client.post('/execute', headers=headers('alice'), json={'command': 'python big.py'}).status_code == 507
        assert client.post('/execute', headers=headers('alice'), json={'command': 'rm -rf old'}).status_code == 200
        assert client.get('/files/list', headers=headers('alice')).status_code == 200


def test_proxy_streams_files():
    pool, _, _ = make_pool()
    app, _ = make_app(pool)
    with TestClient(app) as client:
        r = client.get('/files/download', headers=headers('alice'))
    assert r.content == b'x' * 10
    assert not pool.active  # the terminal is free again after the stream


class FakeUpstream:
    def __init__(self, sent):
        self.sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, message):
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if len(self.sent) >= 2 and not getattr(self, 'replied', False):
            self.replied = True
            return 'output from terminal'
        await asyncio.sleep(0.05)
        if getattr(self, 'replied', False):
            raise StopAsyncIteration
        return await self.__anext__()

    async def close(self):
        pass


def test_websocket_first_message_auth_is_swapped_for_the_terminal_key():
    pool, _, _ = make_pool()
    sent, targets = [], []

    def connect(url, **kwargs):
        targets.append((url, kwargs['additional_headers']))
        return FakeUpstream(sent)

    proxy = Proxy(pool.s, pool, transport=httpx.MockTransport(lambda r: httpx.Response(200)), ws_connect=connect)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.websocket('/{path:path}')
    async def ws(websocket: WebSocket, path: str):
        await proxy.websocket(websocket, path)

    with TestClient(app) as client:
        with client.websocket_connect('/api/terminals/s1?user_id=alice', headers={'X-User-Id': 'alice'}) as w:
            w.send_text(json.dumps({'type': 'auth', 'token': 'broker-key'}))
            w.send_text('ls\n')
            assert w.receive_text() == 'output from terminal'
    first = json.loads(sent[0])
    assert first['token'] != 'broker-key' and first['type'] == 'auth' and sent[1] == 'ls\n'
    assert targets[0][0].startswith(f'ws://term-{user_hash("alice")}:8000/api/terminals/s1')

    with TestClient(app) as client:
        with client.websocket_connect('/events', headers={'X-User-Id': 'alice'}) as w:
            w.send_text(json.dumps({'type': 'auth', 'token': 'wrong'}))
            with pytest.raises(Exception):
                w.receive_text()


# ---------------------------------------------------------------- egress log


def test_egress_log_is_attributed_to_users_and_alerts_on_uploads():
    lines = [
        '1000.0 172.31.250.11 CONNECT pypi.org:443 200 50000 2000',
        '1001.0 172.31.250.11 CONNECT files.pythonhosted.org:443 200 900000 1000',
        '1500.0 172.31.250.12 CONNECT paste.example:443 200 1000 60000000',
        '1600.0 172.31.250.12 CONNECT 192.168.1.5:443 403 0 0',
        '2500.0 172.31.250.11 GET http://example.com/ 200 100 100',  # IP reused by bob after alice left
    ]
    leases = [
        {'ip': '172.31.250.11', 'user_id': 'alice', 'start': 900, 'end': 2000},
        {'ip': '172.31.250.12', 'user_id': 'bob', 'start': 900, 'end': None},
        {'ip': '172.31.250.11', 'user_id': 'bob', 'start': 2400, 'end': None},
    ]
    result = egress.summary(egress.parse(lines, 0), leases, upload_alert_mb=50)
    users = {u['user_id']: u for u in result['users']}
    assert users['alice']['requests'] == 2 and users['alice']['hosts'][0]['host'] == 'pypi.org'
    assert users['bob']['requests'] == 3 and users['bob']['denied'] == 1
    assert [a['user_id'] for a in result['alerts']] == ['bob']


def test_browser_view_websocket_authenticates_with_the_header():
    pool, _, _ = make_pool()
    sent, targets = [], []

    class Upstream(FakeUpstream):
        async def __anext__(self):
            if not getattr(self, 'replied', False):
                self.replied = True
                return 'frame'
            await asyncio.sleep(0.05)
            raise StopAsyncIteration

    def connect(url, **kwargs):
        targets.append(kwargs['additional_headers'])
        return Upstream(sent)

    proxy = Proxy(pool.s, pool, transport=httpx.MockTransport(lambda r: httpx.Response(200)), ws_connect=connect)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.websocket('/{path:path}')
    async def ws(websocket: WebSocket, path: str):
        await proxy.websocket(websocket, path)

    with TestClient(app) as client:
        with client.websocket_connect('/browser/view/s1/cap/ws',
                                      headers={'X-User-Id': 'alice', 'Authorization': 'Bearer broker-key'}) as w:
            assert w.receive_text() == 'frame'
    assert sent == []  # no first-message auth injected
    assert targets[0]['Authorization'].startswith('Bearer ') and 'broker-key' not in targets[0]['Authorization']
