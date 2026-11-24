"""The few Docker Engine API calls the broker needs, over HTTP to docker-socket-proxy.

Only containers, volumes and exec (to measure disk use) are used; the proxy is configured to allow nothing else.
Every resource the broker creates carries the label ncs.term=1, and the broker only ever acts on its own names.
"""

from __future__ import annotations

from typing import Optional

import httpx

LABEL = 'ncs.term'


class DockerError(RuntimeError):
    pass


class DockerApi:
    def __init__(self, base_url: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, ok: tuple = (200, 201, 204, 304), **kwargs) -> httpx.Response:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code not in ok:
            raise DockerError(f'{method} {path}: {response.status_code} {response.text[:300]}')
        return response

    # ------------------------------------------------------------ volumes

    async def create_volume(self, name: str, labels: dict) -> None:
        await self._call('POST', '/volumes/create', json={'Name': name, 'Labels': {LABEL: '1', **labels}})

    async def remove_volume(self, name: str) -> None:
        await self._call('DELETE', f'/volumes/{name}', ok=(204, 404))

    # ------------------------------------------------------------ containers

    async def inspect(self, name: str) -> Optional[dict]:
        response = await self._client.get(f'/containers/{name}/json')
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DockerError(f'inspect {name}: {response.status_code} {response.text[:300]}')
        return response.json()

    async def create(self, name: str, spec: dict) -> None:
        await self._call('POST', '/containers/create', params={'name': name}, json=spec)

    async def start(self, name: str) -> None:
        await self._call('POST', f'/containers/{name}/start')

    async def stop(self, name: str, timeout: int = 10) -> None:
        await self._call('POST', f'/containers/{name}/stop', params={'t': timeout}, ok=(204, 304, 404),
                         timeout=timeout + 30)

    async def remove(self, name: str) -> None:
        await self._call('DELETE', f'/containers/{name}', params={'force': 'true'}, ok=(204, 404))

    async def list(self) -> list[dict]:
        response = await self._call('GET', '/containers/json',
                                    params={'all': 'true', 'filters': f'{{"label":["{LABEL}=1"]}}'})
        return response.json()

    async def exec(self, name: str, cmd: list[str], timeout: int = 60) -> str:
        created = await self._call('POST', f'/containers/{name}/exec',
                                   json={'Cmd': cmd, 'AttachStdout': True, 'AttachStderr': True, 'Tty': True})
        exec_id = created.json()['Id']
        output = await self._call('POST', f'/exec/{exec_id}/start', json={'Detach': False, 'Tty': True},
                                  timeout=timeout)
        return output.content.decode('utf-8', 'replace')
