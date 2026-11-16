"""Whether attached files are still being repaired, and waiting (bounded) for them before an answer.

A file is "ready" once its text no longer changes: the pipeline is past the repair and rebuild stages (or never
ran). Indexing and the precomputed summary may still be running; answers do not need to wait for them.
"""

import asyncio
import time
from typing import Awaitable, Callable, Optional

from open_webui.file_pipeline.models import FileJobs

READY_STAGES = {'index', 'summary'}
STAGE_LABELS = {
    None: 'chờ xử lý',
    'pages': 'đang chấm điểm OCR',
    'repair': 'đang sửa OCR',
    'rebuild': 'đang ghép lại văn bản',
}


async def processing_state(file_id: str) -> Optional[dict]:
    """None when the file text is final, else {'stage', 'done', 'total', 'label'}."""
    job = await FileJobs.get(file_id)
    if not job or job.status in ('done', 'failed', 'skipped') or job.stage in READY_STAGES:
        return None
    progress = job.progress or {}
    label = STAGE_LABELS.get(job.stage if job.status == 'running' else None, 'đang xử lý')
    done, total = progress.get('done') or 0, progress.get('total') or 0
    if job.stage == 'repair' and total:
        label = f'{label} {done}/{total} trang'
    return {'stage': job.stage, 'done': done, 'total': total, 'label': label}


async def wait_until_ready(
    files: list[dict], timeout: int, emit: Optional[Callable[[str], Awaitable[None]]] = None
) -> dict[str, dict]:
    """Wait up to timeout seconds for the files' text to be final; returns {file_id: state} of those still not."""
    pending = {}
    for item in files:
        if item.get('id'):
            state = await processing_state(item['id'])
            if state:
                pending[item['id']] = (item.get('name') or item.get('filename') or 'file', state)
    deadline = time.monotonic() + max(timeout, 0)
    last = ''
    while pending and time.monotonic() < deadline:
        if emit:
            text = '; '.join(f'{name}: {state["label"]}' for name, state in pending.values())
            if text != last:
                await emit(f'Đang xử lý file trước khi trả lời ({text})…')
                last = text
        await asyncio.sleep(2)
        for file_id, (name, _) in list(pending.items()):
            state = await processing_state(file_id)
            if state:
                pending[file_id] = (name, state)
            else:
                del pending[file_id]
    return {file_id: state for file_id, (_, state) in pending.items()}
