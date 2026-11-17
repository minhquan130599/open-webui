"""Stages of one file's background processing.

1. pages   score every PDF page's extracted text (file_page rows keep the first extraction).
2. repair  pages under the minimum score are transcribed from their image by a vision model; the new text is
           scored too, so a repair that is not better is kept aside and the page stays "unverified".
3. rebuild the file text is rebuilt from the page texts in use (page offsets, scores and methods recomputed).
4. index   the file's search chunks are rebuilt by article/section with their location and page.
5. summary large files get their whole-file summary computed ahead, in the same cache the chat tool reads.

Every stage is safe to repeat: a job queued again re-runs from the start and keeps earlier repairs while the
file text is the one this pipeline built.
"""

import asyncio
import logging
import time
from typing import Optional

from langchain_core.documents import Document

from open_webui.file_pipeline.models import FileJobs, FilePages
from open_webui.file_pipeline.settings import file_settings
from open_webui.utils.file_structure import (
    build_file_structure,
    document_profile,
    get_structure,
    is_pdf,
    is_spreadsheet,
    render_pdf_page,
    score_pages,
)

log = logging.getLogger(__name__)

LEASE_SECONDS = 600

REPAIR_PROMPT = """This is page {page} of the document "{name}". Transcribe ALL the text printed on the page image
exactly as written, in reading order, as Markdown: keep headings (Chương, Mục, Điều...), numbered items and tables
(Markdown tables). Keep every number, date, name and diacritic exactly, in the language(s) printed on the page. Do not
translate, summarize, correct or add anything. Write [không đọc được] where a part cannot be read. Output only the page
text.{language}{ocr}"""

# Only for documents in Latin script (Vietnamese, English...): OCR engines tend to invent CJK glyphs there.
LATIN_ONLY = """
The document is written in Latin letters (Vietnamese and/or English): never output Chinese, Japanese, Korean or other
non-Latin characters unless they are visibly printed on the page."""

# Only with files.repair.ocr_hint: vision models tend to copy the OCR errors they are shown.
OCR_HINT = """

An OCR engine read this page as below. It contains errors (including invented foreign characters); use it only to
locate text, and write what the image shows:
<ocr>
{ocr}
</ocr>"""

RETRY_NOTE = """

Your previous transcription contained characters that are not on the page (for example Chinese characters). Read the
image again and write only what is printed."""


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class Job:
    """Progress reporting for one claimed job (also keeps the lease alive)."""

    def __init__(self, file_id: str, lease_id: str):
        self.file_id = file_id
        self.lease_id = lease_id
        self.stats: dict = {}

    async def stage(self, name: str, total: int = 0, label: str = '') -> None:
        await FileJobs.update(
            self.file_id,
            self.lease_id,
            LEASE_SECONDS,
            stage=name,
            progress={'done': 0, 'total': total, 'label': label},
            stats=self.stats,
        )

    async def progress(self, done: int, total: int, label: str = '') -> None:
        await FileJobs.update(
            self.file_id, self.lease_id, LEASE_SECONDS, progress={'done': done, 'total': total, 'label': label}
        )


async def _page_rows(file, content: str, structure: dict, min_score: int, keep_existing: bool) -> list:
    """file_page rows for the current file text, keeping earlier repairs when the text is the rebuilt one."""
    pages = structure.get('pages') or []
    existing = {row.page: row for row in await FilePages.list(file.id)}
    if keep_existing and existing and set(existing) == {page for page, _, _ in pages}:
        # Keep earlier repairs, but judge every page again with the current scorer and threshold.
        rows = [existing[page] for page in sorted(existing)]
        raw_scores = score_pages([row.raw_text or '' for row in rows])
        current = score_pages([row.text or '' for row in rows])
        for row, raw, now in zip(rows, raw_scores, current):
            good = now['score'] >= min_score
            if row.status in ('repaired', 'unverified'):
                status = 'repaired' if good else 'unverified'
            elif row.status == 'failed':
                status = 'failed'
            else:
                status = 'ok' if good else 'low'
            await FilePages.save(
                file.id,
                row.page,
                status=status,
                score=now['score'],
                raw_score=raw['score'],
                issues={'issues': now['issues'], **{k: v for k, v in now.items() if k not in ('score', 'issues')}},
            )
        return await FilePages.list(file.id)

    texts = [content[start:end] for _, start, end in pages]
    scores = score_pages(texts)
    methods = structure.get('page_methods') or {}
    rows = [
        {
            'page': page,
            'method': methods.get(str(page)),
            'status': 'ok' if result['score'] >= min_score else 'low',
            'score': result['score'],
            'raw_score': result['score'],
            'issues': {'issues': result['issues'], **{k: v for k, v in result.items() if k not in ('score', 'issues')}},
            'raw_text': text,
            'text': text,
        }
        for (page, _, _), text, result in zip(pages, texts, scores)
    ]
    await FilePages.replace_all(file.id, rows)
    return await FilePages.list(file.id)


async def _repair_pages(
    job: Job,
    file,
    rows: list,
    settings: dict,
    only_pages: Optional[list],
    force: bool = False,
    engine: Optional[str] = None,
) -> int:
    """Transcribe low pages from their images; returns how many pages now use the vision text."""
    from open_webui.storage.provider import Storage
    from open_webui.tracker.llm import generate_text

    min_score = settings['files.ocr.min_score']
    if only_pages:
        targets = [row for row in rows if row.page in set(only_pages)]
    else:
        # A manual re-run also retries pages a previous repair could not make good.
        retry = ('low', 'unverified', 'failed') if force else ('low',)
        targets = [row for row in rows if row.status in retry]
    targets = targets[: settings['files.repair.max_pages']]
    if not targets:
        return 0
    pdf_path = await asyncio.to_thread(Storage.get_file, file.path)
    # A repaired page is judged in the context of the whole document (Vietnamese? Chinese or bilingual?).
    profile = document_profile([row.text or '' for row in rows])
    semaphore = asyncio.Semaphore(settings['files.repair.concurrency'])
    model = settings['files.repair.model'] or None
    max_side = settings['files.page_image_max_side']
    done = changed = 0
    await job.stage('repair', len(targets), 'Sửa trang OCR kém bằng model vision')
    # Repaired pages reach the file text as they come (at most every few seconds), so a question asked
    # mid-way already reads them; the full rebuild after this stage settles the final text.
    rebuild_lock = asyncio.Lock()
    last_rebuild = 0.0

    async def publish() -> None:
        nonlocal last_rebuild
        async with rebuild_lock:
            if time.monotonic() - last_rebuild < 5:
                return
            last_rebuild = time.monotonic()
            await _rebuild(file, await FilePages.list(file.id), settings, engine)

    async def repair(row) -> None:
        nonlocal done, changed
        started = time.perf_counter()
        record = {'model': model or 'tracker', 'at': time.time_ns(), 'score_before': row.score}
        try:
            image, _ = await asyncio.to_thread(render_pdf_page, pdf_path, row.page, max_side)
            hint = OCR_HINT.format(ocr=(row.raw_text or '')[:6000]) if settings['files.repair.ocr_hint'] else ''
            language = '' if profile['foreign_is_content'] else LATIN_ONLY
            prompt = REPAIR_PROMPT.format(page=row.page, name=file.filename, language=language, ocr=hint)
            attempts = 0
            while True:
                attempts += 1
                async with semaphore:
                    text = await generate_text(
                        [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': image}}]}],
                        max_tokens=4096,
                        model=model,
                        purpose='file_repair',
                        disable_thinking=settings['files.summary.disable_thinking'],
                    )
                text = (text or '').strip()
                if not text:
                    raise ValueError('model returned no text')
                result = score_pages([text], profile)[0]
                # Invented foreign glyphs: ask once more, telling the model what went wrong.
                if 'foreign_script' not in result['issues'] or attempts >= 2:
                    break
                prompt += RETRY_NOTE
            record['ms'] = _ms(started)
            record['attempts'] = attempts
            record.update(score_after=result['score'], issues_after=result['issues'])
            better = result['score'] >= (row.raw_score or 0)
            await FilePages.save(
                file.id,
                row.page,
                status='repaired' if result['score'] >= min_score else 'unverified',
                method='vision' if better else row.method,
                text=text if better else row.raw_text,
                score=result['score'] if better else row.raw_score,
                repair={**record, 'text': text, 'used': better},
            )
            changed += better
            if better:
                await publish()
        except Exception as error:
            record.update(ms=_ms(started), error=str(error)[:500])
            log.warning('File %s page %s: repair failed (%s)', file.id, row.page, error)
            await FilePages.save(file.id, row.page, status='failed', repair=record)
        done += 1
        await job.progress(done, len(targets), 'Sửa trang OCR kém bằng model vision')

    await asyncio.gather(*(repair(row) for row in targets))
    return changed


async def _rebuild(file, rows: list, settings: dict, engine: Optional[str]) -> tuple[str, dict]:
    """File text and structure rebuilt from the page texts in use."""
    from open_webui.models.files import Files

    docs = [
        Document(page_content=row.text or '', metadata={'page': row.page - 1, 'extraction': row.method or 'ocr'})
        for row in rows
    ]
    content = '\n'.join(doc.page_content for doc in docs)
    structure = build_file_structure(
        docs,
        content,
        file.filename or '',
        (file.meta or {}).get('content_type'),
        min_score=settings['files.ocr.min_score'],
        engine=engine,
    )
    from open_webui.utils.misc import calculate_sha256_string

    content_hash = calculate_sha256_string(content)
    await Files.update_file_data_by_id(file.id, {'content': content, 'structure': structure, 'summaries': None})
    await Files.update_file_hash_by_id(file.id, content_hash)
    return content, structure or {}


async def _index(app, file, content: str, structure: dict, settings: dict) -> int:
    """Rebuild the file's search chunks (its own collection and every knowledge base holding it)."""
    from starlette.concurrency import run_in_threadpool

    from open_webui.file_pipeline.chunking import structure_chunks
    from open_webui.models.config import Config
    from open_webui.models.knowledge import Knowledges
    from open_webui.models.users import Users
    from open_webui.monitoring.reindex import internal_request
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
    from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS, save_docs_to_vector_db
    from open_webui.utils.misc import calculate_sha256_string

    config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values())
    if config.get('rag.bypass_embedding_and_retrieval'):
        return 0
    base = {'name': file.filename, 'created_by': file.user_id, 'file_id': file.id, 'source': file.filename}
    if settings['files.chunk.by_structure']:
        docs = structure_chunks(file.filename, content, structure, settings['files.chunk.max_chars'])
        split = False
    else:
        docs = [Document(page_content=content, metadata={})]
        split = True
    docs = [Document(page_content=doc.page_content, metadata={**doc.metadata, **base}) for doc in docs]
    metadata = {'file_id': file.id, 'name': file.filename, 'hash': calculate_sha256_string(content)}
    request = internal_request(app)
    admin = await Users.get_super_admin_user()
    await run_in_threadpool(
        save_docs_to_vector_db,
        request,
        docs=docs,
        collection_name=f'file-{file.id}',
        config=config,
        metadata=metadata,
        overwrite=True,
        split=split,
        user=admin,
    )
    for knowledge in await Knowledges.get_knowledge_bases():
        if file.id not in {f.id for f in await Knowledges.get_files_by_id(knowledge.id)}:
            continue
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge.id, filter={'file_id': file.id})
        except Exception:
            pass
        await run_in_threadpool(
            save_docs_to_vector_db,
            request,
            docs=docs,
            collection_name=knowledge.id,
            config=config,
            metadata=metadata,
            add=True,
            split=split,
            user=admin,
        )
    return len(docs)


async def _summarize(job: Job, file, structure: dict, settings: dict) -> bool:
    from open_webui.file_pipeline.summary import cached_summary, store_summary, summarize_file
    from open_webui.tracker.llm import generate_text

    if cached_summary(file, None):
        return False
    model = settings['files.summary.model'] or None

    async def complete(message) -> str:
        return await generate_text(
            [{'role': 'user', 'content': message}],
            max_tokens=4096,
            model=model,
            purpose='file_summary',
            disable_thinking=settings['files.summary.disable_thinking'],
        )

    async def emit(description: str, finished: bool = False) -> None:
        await job.progress(0, 0, description)

    result = await summarize_file(
        file,
        structure,
        complete,
        segment_chars=settings['files.summary.segment_chars'],
        concurrency=settings['files.summary.concurrency'],
        max_chars=settings['files.summary.max_chars'],
        emit=emit,
    )
    await store_summary(file, None, result)
    return True


async def run_job(app, file_id: str, lease_id: str, options: dict) -> None:
    from open_webui.models.config import Config
    from open_webui.models.files import Files

    job = Job(file_id, lease_id)
    started_all = time.perf_counter()
    try:
        settings = await file_settings()
        file = await Files.get_file_by_id(file_id)
        content = ((file.data or {}).get('content') or '') if file else ''
        if not file or not content.strip():
            await FileJobs.finish(file_id, lease_id, 'skipped', {'reason': 'no text'})
            return
        structure = get_structure(file)
        pdf = is_pdf(file.filename, (file.meta or {}).get('content_type')) and bool(file.path)
        engine = await Config.get('rag.content_extraction_engine')
        previous = await FileJobs.get(file_id)
        rebuilt_hash = ((previous.stats or {}) if previous else {}).get('rebuilt_hash')
        job.stats = {'chars': len(content), 'pdf': pdf}

        # 1. pages
        rows = []
        if pdf and structure.get('pages'):
            started = time.perf_counter()
            await job.stage('pages', len(structure['pages']), 'Chấm điểm OCR từng trang')
            rows = await _page_rows(
                file, content, structure, settings['files.ocr.min_score'], keep_existing=rebuilt_hash == file.hash
            )
            job.stats['pages'] = len(rows)
            job.stats['low_pages'] = sum(row.status == 'low' for row in rows)
            job.stats['pages_ms'] = _ms(started)

        # 2. repair
        changed = 0
        if rows and settings['files.repair.enable']:
            started = time.perf_counter()
            changed = await _repair_pages(
                job, file, rows, settings, options.get('pages'), bool(options.get('force')), engine
            )
            job.stats['repaired_pages'] = changed
            job.stats['repair_ms'] = _ms(started)
            rows = await FilePages.list(file_id)

        # 3. rebuild
        if changed or options.get('pages'):
            await job.stage('rebuild', 0, 'Ghép lại văn bản từ các trang đã sửa')
            content, structure = await _rebuild(file, rows, settings, engine)
            file = await Files.get_file_by_id(file_id)
            job.stats['rebuilt_hash'] = file.hash
            job.stats['chars'] = len(content)
        elif rebuilt_hash and rebuilt_hash == file.hash:
            job.stats['rebuilt_hash'] = rebuilt_hash
        job.stats['unverified_pages'] = sum(row.status in ('low', 'unverified', 'failed') for row in rows)

        # 4. index
        if changed or options.get('pages') or settings['files.chunk.by_structure']:
            started = time.perf_counter()
            await job.stage('index', 0, 'Lập chỉ mục theo Chương/Mục/Điều')
            job.stats['chunks'] = await _index(app, file, content, structure, settings)
            job.stats['index_ms'] = _ms(started)

        # 5. summary: optional, the chat tool can still summarize on demand, so a failure does not fail the job.
        # Spreadsheets get no prose summary: rows flattened into text make a slow, useless one; they are read
        # with query_table and their task tables go to the tracker row by row.
        if (
            settings['files.summary.precompute']
            and len(content) >= settings['files.summary.precompute_min_chars']
            and not is_spreadsheet(file.filename, (file.meta or {}).get('content_type'))
        ):
            started = time.perf_counter()
            await job.stage('summary', 0, 'Tóm tắt sẵn')
            try:
                if await _summarize(job, file, structure, settings):
                    job.stats['summary_ms'] = _ms(started)
            except Exception as error:
                log.warning('File %s: summary not precomputed (%s)', file_id, error)
                job.stats['summary_error'] = f'{type(error).__name__}: {error}'[:500]
                job.stats['summary_ms'] = _ms(started)

        job.stats['total_ms'] = _ms(started_all)
        await FileJobs.finish(file_id, lease_id, 'done', job.stats)
        log.info('File pipeline done for %s (%s): %s', file.filename, file_id, job.stats)
    except Exception as error:
        log.exception('File pipeline failed for %s', file_id)
        job.stats['total_ms'] = _ms(started_all)
        await FileJobs.finish(file_id, lease_id, 'failed', job.stats, f'{type(error).__name__}: {error}')
