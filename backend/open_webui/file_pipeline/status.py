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
    """None when the file text is final, else {'stage', 'done', 'total', 'label'}.

    Only PDFs with pages under the OCR threshold get new text; any other file (CSV, Markdown, Word, a clean
    PDF) is final as soon as it is uploaded, even while its index or summary is still being built.
    """
    from open_webui.models.files import Files
    from open_webui.utils.file_structure import get_structure, is_pdf

    file = await Files.get_file_by_id(file_id)
    if not file:
        return None
    # Uploads return before the text is extracted: a message sent right away must wait for it, any file type.
    if (file.data or {}).get('status') in ('pending', 'processing'):
        return {'stage': 'extract', 'done': 0, 'total': 0, 'label': 'đang trích xuất nội dung'}
    job = await FileJobs.get(file_id)
    if not job or job.status in ('done', 'failed', 'skipped') or job.stage in READY_STAGES:
        return None
    if job.stage not in ('repair', 'rebuild'):
        if not is_pdf(file.filename, (file.meta or {}).get('content_type')):
            return None
        if not get_structure(file).get('low_quality_pages'):
            return None
    progress = job.progress or {}
    label = STAGE_LABELS.get(job.stage if job.status == 'running' else None, 'đang xử lý')
    done, total = progress.get('done') or 0, progress.get('total') or 0
    if job.stage == 'repair' and total:
        label = f'{label} {done}/{total} trang'
    return {'stage': job.stage, 'done': done, 'total': total, 'label': label}


async def wait_until_ready(
    files: list[dict],
    timeout: int,
    emit: Optional[Callable[[str], Awaitable[None]]] = None,
    extract_timeout: int = 180,
) -> dict[str, dict]:
    """Wait for the files' text to be final; returns {file_id: state} of those still not.

    Text extraction is always waited for (up to extract_timeout): without it the model gets no text at all.
    OCR repair is waited for up to timeout (files.wait_seconds, 0 = not at all).
    """
    pending = {}
    for item in files:
        if item.get('id'):
            state = await processing_state(item['id'])
            if state:
                pending[item['id']] = (item.get('name') or item.get('filename') or 'file', state)
    # Someone is waiting for these files: they go ahead of background work in the queue.
    for file_id, (_, state) in pending.items():
        if state['stage'] != 'extract':
            await FileJobs.prioritize(file_id)
    started = time.monotonic()
    given_up = {}
    last = ''

    def expired(state: dict) -> bool:
        limit = extract_timeout if state['stage'] == 'extract' else timeout
        return time.monotonic() - started >= max(limit, 0)

    while pending:
        for file_id, (name, state) in list(pending.items()):
            if expired(state):
                given_up[file_id] = state
                del pending[file_id]
        if not pending:
            break
        if emit:
            text = '; '.join(f'{name}: {state["label"]}' for name, state in pending.values())
            if text != last:
                await emit(f'Đang xử lý file trước khi trả lời ({text})…')
                last = text
        await asyncio.sleep(2)
        for file_id, (name, previous) in list(pending.items()):
            state = await processing_state(file_id)
            if state:
                if previous['stage'] == 'extract' and state['stage'] != 'extract':
                    await FileJobs.prioritize(file_id)
                pending[file_id] = (name, state)
            else:
                del pending[file_id]
    return given_up
