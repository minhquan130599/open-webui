"""Monitor pages for the file pipeline: processing of each upload, page OCR scores and vision repairs.

The router is included behind the page login. Lists and scores need nothing more; page text and images are
document content, so they need the reveal key (or an admin edit session), like the knowledge file viewer.
Re-running the pipeline needs an admin edit session.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.monitoring.server.admin import require_session

router = APIRouter()


def _ms(ns):
    return int(ns // 1_000_000) if ns else None


@router.get('/api/files/jobs')
async def file_jobs(range: str = '7d', status: Optional[str] = None, q: str = ''):
    from open_webui.monitoring.server.app import clean, rows, window

    start, _, _ = window(range)
    where = ['j.queued_at >= :start']
    params = {'start': start * 1_000_000}
    if status:
        where.append('j.status = :status')
        params['status'] = status
    if q.strip():
        where.append('(f.filename ILIKE :q OR j.file_id ILIKE :q)')
        params['q'] = f'%{q.strip()}%'
    items = await rows(
        f"""SELECT j.file_id, j.status, j.stage, j.progress, j.stats, j.last_error, j.fail_count,
               j.queued_at, j.started_at, j.finished_at, f.filename, f.meta::jsonb->>'size' AS size,
               f.meta::jsonb->>'content_type' AS content_type, coalesce(u.name, u.email) AS user_name,
               p.total, p.ok, p.low, p.repaired, p.unverified, p.failed, p.avg_raw, p.avg_score
           FROM file_job j
           LEFT JOIN file f ON f.id = j.file_id
           LEFT JOIN "user" u ON u.id = f.user_id
           LEFT JOIN (
               SELECT file_id, count(*) AS total,
                   count(*) FILTER (WHERE status = 'ok') AS ok,
                   count(*) FILTER (WHERE status = 'low') AS low,
                   count(*) FILTER (WHERE status = 'repaired') AS repaired,
                   count(*) FILTER (WHERE status = 'unverified') AS unverified,
                   count(*) FILTER (WHERE status = 'failed') AS failed,
                   avg(raw_score) AS avg_raw, avg(score) AS avg_score
               FROM file_page GROUP BY file_id
           ) p ON p.file_id = j.file_id
           WHERE {' AND '.join(where)}
           ORDER BY j.queued_at DESC LIMIT 200""",
        **params,
    )
    for item in items:
        for key in ('queued_at', 'started_at', 'finished_at'):
            item[key] = _ms(item[key])
    counts = await rows(
        'SELECT status, count(*) AS n FROM file_job WHERE queued_at >= :start GROUP BY status', start=start * 1_000_000
    )
    return {'items': clean(items), 'counts': {r['status']: r['n'] for r in counts}}


@router.get('/api/files/{file_id}/pages')
async def file_pages(file_id: str):
    """File, its processing record and every page's method, scores, issues and repair (without the text)."""
    from open_webui.monitoring.server.app import clean, rows

    file = await rows(
        """SELECT f.id, f.filename, f.meta::jsonb->>'size' AS size, f.meta::jsonb->>'content_type' AS content_type,
               coalesce(u.name, u.email) AS user_name, f.created_at
           FROM file f LEFT JOIN "user" u ON u.id = f.user_id WHERE f.id = :id""",
        id=file_id,
    )
    if not file:
        raise HTTPException(404, 'Không tìm thấy file')
    job = await rows(
        """SELECT status, stage, progress, stats, last_error, fail_count, queued_at, started_at, finished_at
           FROM file_job WHERE file_id = :id""",
        id=file_id,
    )
    pages = await rows(
        """SELECT page, method, status, score, raw_score, issues, length(raw_text) AS raw_chars, length(text) AS chars,
               (repair::jsonb - 'text') AS repair
           FROM file_page WHERE file_id = :id ORDER BY page""",
        id=file_id,
    )
    if job:
        for key in ('queued_at', 'started_at', 'finished_at'):
            job[0][key] = _ms(job[0][key])
    return {'file': clean(file)[0], 'job': clean(job)[0] if job else None, 'pages': clean(pages)}


async def _page_text(file_id: str, page: int) -> dict:
    from open_webui.monitoring.server.app import clean, rows

    found = await rows(
        """SELECT page, method, status, score, raw_score, issues, raw_text, text, repair
           FROM file_page WHERE file_id = :id AND page = :page""",
        id=file_id,
        page=page,
    )
    if not found:
        raise HTTPException(404, 'Không có trang này')
    return clean(found)[0]


async def _page_image(file_id: str, page: int):
    import aiohttp
    from fastapi.responses import Response

    from open_webui.monitoring.server.app import MONITOR_API_KEY, OPEN_WEBUI_URL, webui_error

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(
                f'{OPEN_WEBUI_URL}/api/v1/monitor/files/{file_id}/pages/{page}/image',
                headers={'X-Monitor-Key': MONITOR_API_KEY},
            ) as response:
                if response.status != 200:
                    try:
                        detail = (await response.json()).get('detail')
                    except Exception:
                        detail = None
                    raise HTTPException(response.status, detail or 'Không lấy được ảnh trang từ NCS AI')
                body = await response.read()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, webui_error(e, 60))
    return Response(body, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.get('/api/files/{file_id}/pages/{page}/text')
async def file_page_text_reveal(file_id: str, page: int, request: Request):
    from open_webui.monitoring.server.app import check_reveal

    check_reveal(request, f'file {file_id} page {page}')
    return await _page_text(file_id, page)


@router.get('/api/admin/files/{file_id}/pages/{page}/text')
async def file_page_text_admin(file_id: str, page: int, token: str = Depends(require_session)):
    return await _page_text(file_id, page)


@router.get('/api/files/{file_id}/pages/{page}/image')
async def file_page_image_reveal(file_id: str, page: int, request: Request):
    from open_webui.monitoring.server.app import check_reveal

    check_reveal(request, f'file {file_id} page {page} (image)')
    return await _page_image(file_id, page)


@router.get('/api/admin/files/{file_id}/pages/{page}/image')
async def file_page_image_admin(file_id: str, page: int, token: str = Depends(require_session)):
    return await _page_image(file_id, page)


class PipelineForm(BaseModel):
    pages: list[int] = []


@router.post('/api/admin/files/{file_id}/pipeline')
async def file_pipeline_run(file_id: str, form: PipelineForm, token: str = Depends(require_session)):
    """Queue the file again; with pages, only those pages are re-read by the vision model."""
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/files/{file_id}/pipeline', method='POST', json_body={'pages': form.pages})
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


class BackfillForm(BaseModel):
    days: int = 30
    limit: int = 50


@router.post('/api/admin/files/pipeline/backfill')
async def file_pipeline_backfill(form: BackfillForm, token: str = Depends(require_session)):
    """Queue recent files that were uploaded before the pipeline existed."""
    from open_webui.monitoring.server.app import webui

    result = await webui('/files/pipeline/backfill', method='POST', json_body=form.model_dump())
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


class ReviewForm(BaseModel):
    use: str
    text: Optional[str] = None


@router.post('/api/admin/files/{file_id}/pages/{page}/review')
async def file_page_review(file_id: str, page: int, form: ReviewForm, token: str = Depends(require_session)):
    """Keep the chosen text of a checked page, then rebuild and re-index the file."""
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/files/{file_id}/pages/{page}/review', method='POST', json_body=form.model_dump())
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result
