"""One terminal container per user: created on demand, limited in number, stopped when idle, removed when unused.

Capacity: at most `max_running` terminals run at once. `admin_reserved` of them can only be taken by admins, so an
admin always gets a terminal; admins may use any free place (a second admin takes another free one). When there is no
place, the terminal idle the longest (idle at least `evict_min_idle`) is stopped to make one; when every terminal is
busy, the request waits up to `wait_seconds`, then gets a clear "busy" error.

A terminal counts as busy while a request to it is running or one of its websockets is open.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from broker.config import Settings
from broker.docker_api import LABEL, DockerApi
from broker.store import Store, Terminal

log = logging.getLogger('broker.pool')

SPEC = 'spec'  # the shared terminal that answers requests made without a user (tool list, system prompt)


class Busy(Exception):
    """Every terminal place is taken by a terminal in use."""


class StartFailed(Exception):
    pass


@dataclass
class Endpoint:
    user_hash: str
    url: str
    api_key: str
    over_quota: bool


def user_hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:16]


Probe = Callable[[str, str], Awaitable[bool]]


class Pool:
    def __init__(self, settings: Settings, docker: DockerApi, store: Store, probe: Probe,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable] = asyncio.sleep):
        self.s = settings
        self.docker = docker
        self.store = store
        self.probe = probe
        self.clock = clock
        self.sleep = sleep
        self.active: dict[str, int] = {}  # user_hash -> requests / websockets in progress
        self.starting: dict[str, str] = {}  # user_hash -> role, reserved places being started
        self._lock = asyncio.Lock()
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._touched: dict[str, float] = {}

    # ------------------------------------------------------------ names

    @staticmethod
    def container_name(h: str) -> str:
        return f'term-{h}'

    @staticmethod
    def volume_name(h: str) -> Optional[str]:
        return None if h == SPEC else f'term-home-{h}'

    def url(self, t: Terminal) -> str:
        return f'http://{t.name}:{self.s.port}'

    # ------------------------------------------------------------ capacity

    def _running(self) -> list[Terminal]:
        return [t for t in self.store.all() if t.state == 'running' and t.user_hash != SPEC]

    def _fits(self, role: str, running_roles: list[str]) -> bool:
        if len(running_roles) >= self.s.max_running:
            return False
        if role == 'admin':
            return True
        users = sum(1 for r in running_roles if r != 'admin')
        return users < self.s.max_running - self.s.admin_reserved

    def _idle_for(self, t: Terminal) -> float:
        return self.clock() - t.last_used

    def _evictable(self, running: list[Terminal]) -> list[Terminal]:
        idle = [t for t in running if not self.active.get(t.user_hash) and self._idle_for(t) >= self.s.evict_min_idle]
        return sorted(idle, key=lambda t: t.last_used)

    async def _reserve(self, h: str, role: str) -> None:
        """Take a place for this user (stopping the longest idle terminal if needed), or wait, or raise Busy."""
        deadline = self.clock() + self.s.wait_seconds
        while True:
            async with self._lock:
                running = [t for t in self._running() if t.user_hash != h]
                roles = [t.role for t in running] + list(self.starting.values())
                if self._fits(role, roles):
                    self.starting[h] = role
                    return
                for victim in self._evictable(running):
                    others = [t.role for t in running if t.user_hash != victim.user_hash] + list(self.starting.values())
                    if self._fits(role, others):
                        log.info('making room: stopping idle terminal %s for %s', victim.user_hash, h)
                        await self._stop(victim, 'evicted')
                        self.starting[h] = role
                        return
            if self.clock() >= deadline:
                self.store.event('busy', None, h)
                raise Busy()
            await self.sleep(2)

    # ------------------------------------------------------------ start / stop

    async def endpoint(self, user_id: str, role: str) -> Endpoint:
        """The running terminal of this user, started (and created) if needed."""
        h = SPEC if user_id == SPEC else user_hash(user_id)
        role = 'admin' if role == 'admin' else 'user'
        lock = self._user_locks.setdefault(h, asyncio.Lock())
        async with lock:
            t = self.store.get(h)
            if t and t.state == 'running':
                if t.role != role and h != SPEC:
                    t.role = role
                    self.store.save(t)
                return Endpoint(h, self.url(t), t.api_key, bool(t.over_quota))
            if h != SPEC:
                await self._reserve(h, role)
            try:
                t = await self._start(h, user_id, role, t)
            finally:
                self.starting.pop(h, None)
            return Endpoint(h, self.url(t), t.api_key, bool(t.over_quota))

    def _spec(self, t: Terminal) -> dict:
        s = self.s
        proxy = s.proxy_url
        env = [
            f'OPEN_TERMINAL_API_KEY={t.api_key}',
            f'OPEN_TERMINAL_MAX_SESSIONS={s.max_sessions}',
            'OPEN_TERMINAL_LOG_RETENTION=604800',
        ]
        if proxy:
            env += [f'HTTP_PROXY={proxy}', f'HTTPS_PROXY={proxy}', f'http_proxy={proxy}', f'https_proxy={proxy}',
                    'NO_PROXY=localhost,127.0.0.1', 'no_proxy=localhost,127.0.0.1']
        tmpfs = {'/tmp': f'rw,nosuid,nodev,size={s.tmp_mb}m'}
        mounts = []
        if t.volume:
            mounts.append({'Type': 'volume', 'Source': t.volume, 'Target': s.home})
        else:  # the shared terminal keeps nothing
            tmpfs[s.home] = f'rw,nosuid,nodev,size={s.tmp_mb}m,uid=1000,gid=1000'
        host = {
            'NetworkMode': s.network,
            'Memory': s.memory_bytes,
            'MemorySwap': s.memory_bytes,  # no swap: over the limit the process is stopped, not the host slowed
            'NanoCpus': int(s.cpus * 1e9),
            'PidsLimit': s.pids,
            'ReadonlyRootfs': s.read_only,
            'Tmpfs': tmpfs,
            'Mounts': mounts,
            'CapDrop': ['ALL'],
            'SecurityOpt': ['no-new-privileges:true'],
            'RestartPolicy': {'Name': 'no'},
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '2'}},
        }
        if s.runtime:
            host['Runtime'] = s.runtime
        return {
            'Image': s.image,
            'Env': env,
            'Labels': {LABEL: '1', f'{LABEL}.user': t.user_hash, f'{LABEL}.role': t.role},
            'HostConfig': host,
        }

    async def _start(self, h: str, user_id: str, role: str, t: Optional[Terminal]) -> Terminal:
        now = self.clock()
        if t is None:
            t = Terminal(user_hash=h, user_id=user_id, role=role, name=self.container_name(h),
                         volume=self.volume_name(h), api_key=secrets.token_urlsafe(32), state='absent',
                         created=now, last_used=now)
        t.role, t.last_used = role, now
        info = await self.docker.inspect(t.name)
        if info is None:
            if t.volume:
                await self.docker.create_volume(t.volume, {f'{LABEL}.user': h})
            t.api_key = secrets.token_urlsafe(32)  # a new container gets a new key
            await self.docker.create(t.name, self._spec(t))
            self.store.event('created', user_id, h)
        if not (info or {}).get('State', {}).get('Running'):
            await self.docker.start(t.name)
        t.state = 'running'
        self.store.save(t)
        info = await self.docker.inspect(t.name) or {}
        ip = (((info.get('NetworkSettings') or {}).get('Networks') or {}).get(self.s.network) or {}).get('IPAddress')
        if ip:
            self.store.lease(ip, user_id, now)
        if not await self._wait_ready(t):
            await self._stop(t, 'start_failed')
            raise StartFailed(f'terminal {h} did not become ready')
        return t

    async def _wait_ready(self, t: Terminal, seconds: int = 60) -> bool:
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            if await self.probe(self.url(t), t.api_key):
                return True
            await self.sleep(1)
        return False

    async def _stop(self, t: Terminal, reason: str) -> None:
        await self.docker.stop(t.name)
        t.state = 'stopped'
        self.store.save(t)
        self.store.release(t.user_id)
        if reason != 'idle_stop' or t.user_hash != SPEC:
            self.store.event(reason, t.user_id, t.user_hash)

    def mark_dead(self, h: str) -> None:
        """The terminal did not answer (stopped outside the broker, killed): the next request starts it again."""
        t = self.store.get(h)
        if t and t.state == 'running':
            t.state = 'stopped'
            self.store.save(t)
            self.store.release(t.user_id)

    # ------------------------------------------------------------ activity

    @asynccontextmanager
    async def using(self, h: str):
        self.active[h] = self.active.get(h, 0) + 1
        self.touch(h, force=True)
        try:
            yield
        finally:
            self.active[h] -= 1
            if not self.active[h]:
                del self.active[h]
            self.touch(h, force=True)

    def touch(self, h: str, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._touched.get(h, 0) < 30:
            return
        self._touched[h] = now
        t = self.store.get(h)
        if t:
            t.last_used = now
            self.store.save(t)

    def event(self, kind: str, h: str, detail: str = '') -> None:
        t = self.store.get(h)
        self.store.event(kind, t.user_id if t else None, detail or h)

    # ------------------------------------------------------------ housekeeping

    async def reconcile(self) -> None:
        """At start: align the stored state with Docker; remove containers the broker has no key for."""
        known = {t.name: t for t in self.store.all()}
        seen = set()
        for c in await self.docker.list():
            name = (c.get('Names') or ['/'])[0].lstrip('/')
            seen.add(name)
            t = known.get(name)
            if t is None:
                log.warning('removing unknown terminal container %s', name)
                await self.docker.remove(name)
                continue
            t.state = 'running' if c.get('State') == 'running' else 'stopped'
            self.store.save(t)
        for name, t in known.items():
            if name not in seen and t.state != 'absent':
                t.state = 'absent'
                self.store.save(t)

    async def reap(self) -> None:
        now = self.clock()
        for t in self.store.all():
            idle = now - t.last_used
            busy = self.active.get(t.user_hash) or t.user_hash in self.starting
            try:
                if t.state == 'running' and not busy and idle >= self.s.idle_stop:
                    await self._stop(t, 'idle_stop')
                elif t.state == 'stopped' and idle >= self.s.stopped_remove:
                    await self.docker.remove(t.name)
                    t.state = 'absent'
                    self.store.save(t)
                if t.state in ('absent', 'stopped') and not busy and idle >= self.s.volume_remove:
                    await self.docker.remove(t.name)
                    if t.volume:
                        await self.docker.remove_volume(t.volume)
                    self.store.delete(t.user_hash)
                    self.store.event('expired', t.user_id, t.user_hash)
            except Exception:
                log.exception('housekeeping of %s failed', t.user_hash)
        self.store.prune_leases(now - 90 * 86400)
        self.store.prune_events(now - 90 * 86400)

    async def check_disk(self) -> None:
        quota_kb = self.s.disk_quota_mb * 1024
        for t in self.store.all():
            if t.state != 'running' or not t.volume:
                continue
            try:
                out = await self.docker.exec(t.name, ['du', '-sk', self.s.home])
                kb = int(out.strip().split()[0])
            except Exception:
                log.warning('could not measure the disk use of %s', t.user_hash)
                continue
            over = 1 if kb > quota_kb else 0
            if over and not t.over_quota:
                self.store.event('over_quota', t.user_id, f'{kb // 1024} MB')
            fresh = self.store.get(t.user_hash) or t
            fresh.disk_kb, fresh.over_quota = kb, over
            self.store.save(fresh)

    async def stop_user(self, h: str) -> bool:
        t = self.store.get(h)
        if not t or t.state != 'running':
            return False
        await self._stop(t, 'stopped_by_admin')
        return True

    async def remove_user(self, h: str) -> bool:
        t = self.store.get(h)
        if not t:
            return False
        await self.docker.remove(t.name)
        if t.volume:
            await self.docker.remove_volume(t.volume)
        self.store.release(t.user_id)
        self.store.delete(h)
        self.store.event('removed_by_admin', t.user_id, h)
        return True

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        now = self.clock()
        terminals = []
        for t in self.store.all():
            if t.user_hash == SPEC:
                continue
            terminals.append({
                'id': t.user_hash,
                'user_id': t.user_id,
                'role': t.role,
                'state': t.state,
                'busy': bool(self.active.get(t.user_hash)),
                'idle_seconds': int(now - t.last_used),
                'disk_mb': round(t.disk_kb / 1024, 1),
                'over_quota': bool(t.over_quota),
                'created': t.created,
                'last_used': t.last_used,
            })
        running = [x for x in terminals if x['state'] == 'running']
        return {
            'running': len(running),
            'max_running': self.s.max_running,
            'admin_reserved': self.s.admin_reserved,
            'limits': {'cpus': self.s.cpus, 'memory_mb': self.s.memory_mb, 'disk_quota_mb': self.s.disk_quota_mb,
                       'idle_stop_minutes': self.s.idle_stop // 60, 'volume_days': self.s.volume_remove // 86400},
            'terminals': terminals,
            'events_24h': self.store.counts(now - 86400),
            'recent_events': self.store.recent_events(30),
        }
