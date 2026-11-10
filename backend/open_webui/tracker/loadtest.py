"""Load test for the project tracker, through the public HTTP API.

Creates N channels, posts M realistic progress messages to each (spread across a few fake members via
the message text), then polls /api/v1/tracker/stats until every channel is summarized. Reports how fast
messages were accepted and how long the tracker needed to catch up.

    python -m open_webui.tracker.loadtest --url http://localhost:8000 --token <admin JWT> \
        --channels 50 --messages 40 --concurrency 20

Use an admin token (Settings > Account > API keys, or the `token` cookie). Channels are named
`loadtest-<run>-<n>`; pass --cleanup to delete them afterwards.
"""

import argparse
import asyncio
import random
import statistics
import time
import uuid

import aiohttp

MEMBERS = ['Lan', 'Minh', 'Hùng', 'Trang', 'Tuấn', 'Ngọc']
TASKS = ['module đăng nhập', 'API thanh toán', 'màn hình báo cáo', 'database', 'tích hợp SSO', 'tài liệu SRS']
TEMPLATES = [
    '{member}: {task} xong khoảng {pct}%',
    '{member}: hôm nay làm tiếp {task}, dự kiến xong trước {date}',
    '{member}: {task} đang bị chặn vì chờ môi trường test',
    '{member}: đã fix lỗi ở {task}, nhờ mọi người review',
    'Đính chính: {task} mới được {pct}% thôi, không phải 100%',
    '{member}: chốt dùng PostgreSQL cho {task}',
    'ok',
    '👍',
]


def fake_message(rng: random.Random) -> str:
    return rng.choice(TEMPLATES).format(
        member=rng.choice(MEMBERS),
        task=rng.choice(TASKS),
        pct=rng.choice(range(10, 100, 10)),
        date=f'2026-12-{rng.randint(1, 28):02d}',
    )


async def call(session: aiohttp.ClientSession, method: str, url: str, **kwargs):
    async with session.request(method, url, **kwargs) as response:
        body = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f'{method} {url} -> {response.status}: {body}')
        return body


async def run(args) -> None:
    base = args.url.rstrip('/') + '/api/v1'
    headers = {'Authorization': f'Bearer {args.token}'}
    run_id = uuid.uuid4().hex[:6]
    rng = random.Random(args.seed)
    semaphore = asyncio.Semaphore(args.concurrency)
    latencies: list[float] = []

    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as session:
        stats = await call(session, 'GET', f'{base}/tracker/stats')
        print(f'tracker before: {stats}')

        channel_ids = []
        for n in range(args.channels):
            channel = await call(session, 'POST', f'{base}/channels/create', json={'name': f'loadtest-{run_id}-{n}'})
            channel_ids.append(channel['id'])
        print(f'created {len(channel_ids)} channels (run {run_id})')

        async def post(channel_id: str, content: str):
            async with semaphore:
                started = time.monotonic()
                await call(session, 'POST', f'{base}/channels/{channel_id}/messages/post', json={'content': content})
                latencies.append(time.monotonic() - started)

        jobs = [post(c, fake_message(rng)) for c in channel_ids for _ in range(args.messages)]
        rng.shuffle(jobs)
        started = time.monotonic()
        await asyncio.gather(*jobs)
        elapsed = time.monotonic() - started
        total = len(jobs)
        latencies.sort()
        print(
            f'posted {total} messages in {elapsed:.1f}s ({total / elapsed:.1f} msg/s); '
            f'post latency p50={statistics.median(latencies) * 1000:.0f}ms '
            f'p95={latencies[int(len(latencies) * 0.95) - 1] * 1000:.0f}ms'
        )

        drain_started = time.monotonic()
        while True:
            await asyncio.sleep(args.poll)
            stats = await call(session, 'GET', f'{base}/tracker/stats')
            pending = 0
            for channel_id in channel_ids:
                state = await call(session, 'GET', f'{base}/tracker/channels/{channel_id}')
                pending += 1 if state['pending_count'] else 0
            waited = time.monotonic() - drain_started
            print(f'  t+{waited:5.0f}s  channels pending={pending}/{len(channel_ids)}  cluster={stats}')
            if pending == 0:
                print(f'all {len(channel_ids)} channels summarized {waited:.0f}s after the last message')
                break
            if waited > args.timeout:
                print(f'timeout after {waited:.0f}s with {pending} channels still pending')
                break

        if args.cleanup:
            for channel_id in channel_ids:
                await call(session, 'DELETE', f'{base}/channels/{channel_id}/delete')
            print('deleted test channels')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--channels', type=int, default=20)
    parser.add_argument('--messages', type=int, default=30, help='messages per channel')
    parser.add_argument('--concurrency', type=int, default=20, help='parallel HTTP posts')
    parser.add_argument('--poll', type=float, default=15)
    parser.add_argument('--timeout', type=float, default=3600, help='seconds to wait for the tracker')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--cleanup', action='store_true')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
