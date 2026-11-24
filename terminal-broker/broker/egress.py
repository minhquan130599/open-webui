"""Who sent what out of the terminals: the egress proxy's access log, attributed to users.

The proxy (squid) writes one line per request in the `ncs` format:
    <epoch> <client ip> <method> <url> <status> <bytes to client> <bytes from client>
Client IPs are terminal containers; the broker's leases say which user held which IP at that time.
"""

from __future__ import annotations

import glob
import os
from collections import defaultdict
from typing import Iterable, Optional
from urllib.parse import urlsplit


def _host(method: str, url: str) -> str:
    if method == 'CONNECT':
        return url.rsplit(':', 1)[0]
    return urlsplit(url).hostname or url


def parse(lines: Iterable[str], since: float) -> list[dict]:
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            ts = float(parts[0])
            status = int(parts[4].split('/')[-1]) if '/' in parts[4] else int(parts[4])
            down, up = int(parts[5]), int(parts[6])
        except ValueError:
            continue
        if ts < since:
            continue
        out.append({'ts': ts, 'ip': parts[1], 'method': parts[2], 'host': _host(parts[2], parts[3]),
                    'status': status, 'down': down, 'up': up})
    return out


def read_log(path: str, since: float) -> list[dict]:
    """The current log and rotated ones (squid names them access.log.0, .1, …) modified after `since`."""
    files = [path] + sorted(glob.glob(path + '.*'))
    records = []
    for name in files:
        try:
            if os.path.getmtime(name) < since:
                continue
            with open(name, encoding='utf-8', errors='replace') as handle:
                records.extend(parse(handle, since))
        except OSError:
            continue
    return records


def owner(ip: str, ts: float, leases: list[dict]) -> Optional[str]:
    for lease in leases:
        if lease['ip'] == ip and lease['start'] <= ts and (lease['end'] is None or ts <= lease['end']):
            return lease['user_id']
    return None


def summary(records: list[dict], leases: list[dict], upload_alert_mb: int) -> dict:
    users: dict[str, dict] = {}
    hourly_up: dict[tuple, int] = defaultdict(int)
    for r in records:
        user = owner(r['ip'], r['ts'], leases) or f'?{r["ip"]}'
        u = users.setdefault(user, {'user_id': user, 'requests': 0, 'denied': 0, 'up_bytes': 0, 'down_bytes': 0,
                                    'hosts': defaultdict(int), 'last': 0})
        u['requests'] += 1
        u['denied'] += r['status'] == 403
        u['up_bytes'] += r['up']
        u['down_bytes'] += r['down']
        u['hosts'][r['host']] += 1
        u['last'] = max(u['last'], r['ts'])
        hourly_up[(user, int(r['ts'] // 3600))] += r['up']
    alerts = [
        {'user_id': user, 'hour_start': hour * 3600, 'up_mb': round(up / 1048576, 1)}
        for (user, hour), up in sorted(hourly_up.items(), key=lambda x: -x[1])
        if up > upload_alert_mb * 1048576
    ]
    out = []
    for u in sorted(users.values(), key=lambda x: -x['up_bytes']):
        hosts = sorted(u.pop('hosts').items(), key=lambda x: -x[1])
        out.append({**u, 'hosts': [{'host': h, 'requests': n} for h, n in hosts[:15]], 'host_count': len(hosts)})
    return {'users': out, 'alerts': alerts}
