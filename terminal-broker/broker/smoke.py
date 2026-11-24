"""Acceptance checks of the terminal setup on a real server, without the AI: run inside the broker container.

    docker exec ncs-terminal-broker python -m broker.smoke            # all checks
    docker exec ncs-terminal-broker python -m broker.smoke --memory   # also fill 3 GB of RAM (slow)

Two test users (smoke-a, smoke-b) get their own terminals through the broker, exactly like Open WebUI would call it,
then the checks run commands in them. Their terminals are removed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

BROKER = os.getenv('SMOKE_BROKER_URL', 'http://127.0.0.1:8000')
KEY = os.getenv('BROKER_API_KEY', '')
ADMIN_KEY = os.getenv('BROKER_ADMIN_KEY', '')
USERS = ('smoke-a', 'smoke-b')


class Terminal:
    def __init__(self, client: httpx.AsyncClient, user: str, execute_path: str):
        self.client, self.user, self.path = client, user, execute_path

    async def run(self, command: str, wait: int = 60) -> tuple[int | None, str, dict]:
        r = await self.client.post(self.path, json={'command': command, 'wait': wait},
                                   headers={'X-User-Id': self.user, 'X-User-Role': 'user'}, timeout=wait + 60)
        data = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
        if r.status_code >= 400:
            return None, str(data.get('error') or r.text)[:500], data
        output = ''.join(str(o.get('data', '')) for o in data.get('output') or [] if isinstance(o, dict))
        return data.get('exit_code'), output, data


def find_execute_path(spec: dict) -> str:
    """The POST operation whose JSON body has a `command` field (Open Terminal's run command)."""
    schemas = spec.get('components', {}).get('schemas', {})

    def props(schema: dict) -> dict:
        if '$ref' in schema:
            schema = schemas.get(schema['$ref'].rsplit('/', 1)[-1], {})
        return schema.get('properties', {})

    for path, ops in spec.get('paths', {}).items():
        body = ((ops.get('post') or {}).get('requestBody') or {}).get('content', {}).get('application/json', {})
        if 'command' in props(body.get('schema') or {}) and '{' not in path:
            return path
    raise RuntimeError('no run-command operation in the terminal openapi.json')


async def main(memory: bool) -> int:
    if not KEY:
        print('BROKER_API_KEY is not set (run inside the broker container)')
        return 2
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        results.append((name, ok, detail))
        print(f'{"PASS" if ok else "FAIL"}  {name}{"" if ok else "  -> " + detail[:300]}')

    async with httpx.AsyncClient(base_url=BROKER, headers={'Authorization': f'Bearer {KEY}'}) as client:
        spec = (await client.get('/openapi.json', timeout=120)).json()
        path = find_execute_path(spec)
        a, b = (Terminal(client, u, path) for u in USERS)

        # Separation between users
        code, _, _ = await a.run('echo smoke-secret > ~/smoke_secret.txt && ls ~/smoke_secret.txt')
        check('A writes a file in their terminal', code == 0)
        code, out, _ = await b.run('cat ~/smoke_secret.txt 2>&1; ls -la /home 2>&1')
        check('B does not see A\'s file', 'smoke-secret' not in out, out)
        code, out, _ = await b.run('hostname; cat /etc/hostname')
        code_a, out_a, _ = await a.run('hostname')
        check('A and B are different machines', out.split()[0:1] != out_a.split()[0:1], f'{out} / {out_a}')

        # Internal network
        for host in ('owui-postgres', 'postgres', 'redis', 'owui-redis', 'rustfs', 'open-webui', 'docker-socket-proxy'):
            code, out, _ = await a.run(f'getent hosts {host} || echo NOT_FOUND')
            check(f'cannot resolve {host}', 'NOT_FOUND' in out, out)
        # The host keeps an address on the terminal bridge: its services must not answer there (terminal-firewall.sh).
        for port in (22, 8000, 8090, 5432, 6379, 9000, 9001, 2375):
            code, out, _ = await a.run(f'timeout 5 bash -c "</dev/tcp/172.31.250.1/{port}" 2>/dev/null && echo OPEN || echo CLOSED')
            check(f'host port {port} not reachable from a terminal', 'OPEN' not in out.split(), out)

        # Internet only through the proxy, never to internal addresses
        code, out, _ = await a.run('curl -s -o /dev/null -w "%{http_code}" --max-time 20 https://pypi.org/simple/pip/')
        check('Internet through the proxy works (pypi.org)', out.strip().endswith('200'), out)
        code, out, _ = await a.run('curl -s -o /dev/null -w "%{http_code}" --noproxy "*" --max-time 10 https://pypi.org/ || true')
        check('no Internet without the proxy', not out.strip().endswith('200'), out)
        for target in ('http://192.168.110.113:8000/', 'http://10.0.0.1/', 'http://172.17.0.1/', 'http://169.254.169.254/'):
            code, out, _ = await a.run(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 15 {target} || true')
            check(f'proxy refuses internal {target}', out.strip() in ('403', '000') or not out.strip().startswith('2'), out)
        code, out, _ = await a.run('curl -s -o /dev/null -w "%{http_code}" --max-time 15 https://smtp.gmail.com:465 || true')
        check('proxy refuses ports other than 80 / 443', not out.strip().startswith('2'), out)

        # Privileges and filesystem
        code, out, _ = await a.run('sudo -n true 2>&1; echo EXIT=$?')
        check('no sudo', 'EXIT=0' not in out, out)
        code, out, _ = await a.run('touch /usr/smoke 2>&1; echo EXIT=$?')
        check('system files are read-only', 'EXIT=0' not in out, out)
        code, out, _ = await a.run('python3 -c "import pandas, openpyxl, docx, pptx, matplotlib; print(\'LIBS_OK\')"')
        check('Python libraries are installed', 'LIBS_OK' in out, out)
        code, out, _ = await a.run('command -v soffice && fc-list | grep -ci noto')
        check('LibreOffice and Vietnamese fonts are installed', code == 0, out)
        code, out, _ = await a.run('cat /sys/fs/cgroup/memory.max 2>/dev/null; cat /sys/fs/cgroup/cpu.max 2>/dev/null')
        check('memory limited to 2 GB', '2147483648' in out, out)

        if memory:
            code, out, data = await a.run('python3 -c "x = bytearray(3 * 1024**3); print(len(x))"', wait=120)
            check('a process over 2 GB is stopped and explained', code in (137, -9) and 'note' in data, str(data)[:300])
            code, out, _ = await a.run('echo STILL_ALIVE')
            check('the terminal still works after a memory kill', 'STILL_ALIVE' in out, out)

        # Clean up the test terminals
        if ADMIN_KEY:
            from broker.pool import user_hash

            for user in USERS:
                await client.delete(f'/_broker/terminals/{user_hash(user)}', headers={'Authorization': f'Bearer {ADMIN_KEY}'})

    failed = [r for r in results if not r[1]]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--memory', action='store_true', help='also check the 2 GB memory limit (allocates 3 GB)')
    sys.exit(asyncio.run(main(parser.parse_args().memory)))
