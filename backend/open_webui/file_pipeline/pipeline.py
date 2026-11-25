"""Stages of one file's background processing.

1. pages   score every PDF page's extracted text (file_page rows keep the first extraction).
2. repair  pages under the minimum score are transcribed from their image by a vision model; the new text is
           scored too, so a repair that is not better is kept aside and the page stays "unverified".
3. rebuild the file text is rebuilt from the page texts in use (page offsets, scores and methods recomputed).
4. index   the file's search chunks are rebuilt by article/section with their location and page.
5. summary large files get their whole-file summary computed ahead, in the same cache the chat tool reads.

The summary does not wait for the other stages: while pages are repaired, the parts whose pages are already final
are summarized (each part is kept on its own), and the index and the rest of the summary run together at the end,
so the final summary only sends the parts that changed. A job queued with {"summary_only": true} (an answer that
could not wait for the whole summary) only finishes the summary.

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
            if row.status == 'checked':
                status = 'checked'  # confirmed by a person on the monitor: never judged again
            elif row.status in ('repaired', 'unverified'):
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


def _repair_targets(rows: list, settings: dict, only_pages: Optional[list], force: bool) -> list:
    """Page rows the repair stage sends to the vision model."""
    if only_pages:
        targets = [row for row in rows if row.page in set(only_pages)]
    else:
        # A manual re-run also retries pages a previous repair could not make good.
        retry = ('low', 'unverified', 'failed') if force else ('low',)
        targets = [row for row in rows if row.status in retry]
    return targets[: settings['files.repair.max_pages']]


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
    targets = _repair_targets(rows, settings, only_pages, force)
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
            # Every page is queued at once behind `concurrency` slots: time spent waiting for a slot is kept apart
            # from the model's own time, or the last pages of a long file look like 10-minute calls.
            attempts = wait_ms = model_ms = 0
            while True:
                attempts += 1
                queued = time.perf_counter()
                async with semaphore:
                    wait_ms += _ms(queued)
                    called = time.perf_counter()
                    text = await generate_text(
                        [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': image}}]}],
                        max_tokens=4096,
                        model=model,
                        purpose='file_repair',
                        disable_thinking=settings['files.summary.disable_thinking'],
                    )
                    model_ms += _ms(called)
                text = (text or '').strip()
                if not text:
                    raise ValueError('model returned no text')
                result = score_pages([text], profile)[0]
                # Invented foreign glyphs: ask once more, telling the model what went wrong.
                if 'foreign_script' not in result['issues'] or attempts >= 2:
                    break
                prompt += RETRY_NOTE
            record.update(ms=_ms(started), wait_ms=wait_ms, model_ms=model_ms, attempts=attempts)
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


async def _summarize(
    job: Job, file, structure: dict, settings: dict, ready=None, parts_only: bool = False
) -> bool:
    """Whole-file summary (or, with parts_only, the parts whose pages are ready), reusing parts made before."""
    from open_webui.file_pipeline.summary import (
        cached_parts,
        cached_summary,
        ensure_plan,
        reuse_summary,
        store_part,
        store_summary,
        summarize_file,
        summary_ceiling,
    )
    from open_webui.tracker.llm import generate_text

    if cached_summary(file, None):
        return False
    model = settings['files.summary.model'] or None

    async def complete(message) -> str:
        return await generate_text(
            [{'role': 'user', 'content': message}],
            # A ceiling, not a length: a part's overview asks for about a tenth of the part. Without one a model
            # that starts repeating itself writes until the context is full (19k tokens for a 12k-token part on W).
            max_tokens=summary_ceiling(message),
            continue_cut=False,
            model=model,
            purpose='file_summary',
            disable_thinking=settings['files.summary.disable_thinking'],
        )

    async def emit(description: str, finished: bool = False) -> None:
        await job.progress(0, 0, description)

    if not parts_only and await reuse_summary(file):
        return True
    result = await summarize_file(
        file,
        structure,
        complete,
        segment_chars=settings['files.summary.segment_chars'],
        concurrency=settings['files.summary.concurrency'],
        ratio_pct=settings['files.summary.ratio_pct'],
        min_chars=settings['files.summary.min_chars'],
        emit=emit,
        plan=await ensure_plan(file, structure, settings['files.summary.segment_chars']),
        part_cache=cached_parts(file),
        save_part=lambda key, text: store_part(file.id, key, text),
        ready=ready,
    )
    if result.get('complete', True) and not parts_only:
        await store_summary(file, None, result)
        return True
    return False


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
        spreadsheet = is_spreadsheet(file.filename, (file.meta or {}).get('content_type'))
        # Spreadsheets get no prose summary: rows flattened into text make a slow, useless one; they are read
        # with query_table and their task tables go to the tracker row by row.
        wants_summary = not spreadsheet and (
            bool(options.get('summary_only'))
            or (settings['files.summary.precompute'] and len(content) >= settings['files.summary.precompute_min_chars'])
        )

        async def summary_stage(current_file, current_structure) -> None:
            """5. summary: optional, the chat tool can still summarize on demand, so a failure does not fail the job."""
            started = time.perf_counter()
            try:
                if await _summarize(job, current_file, current_structure, settings):
                    job.stats['summary_ms'] = _ms(started)
            except Exception as error:
                log.warning('File %s: summary not precomputed (%s)', file_id, error)
                job.stats['summary_error'] = f'{type(error).__name__}: {error}'[:500]
                job.stats['summary_ms'] = _ms(started)

        if options.get('summary_only'):
            # An answer could not wait for the whole summary: finish it, keep the stats of the last full run.
            job.stats = {**((previous.stats or {}) if previous else {}), **job.stats}
            if wants_summary:
                await job.stage('summary', 0, 'Tóm tắt tiếp phần còn lại')
                await summary_stage(file, structure)
            job.stats['total_ms'] = _ms(started_all)
            await FileJobs.finish(file_id, lease_id, 'done', job.stats)
            return

        # 1. pages
        rows = []
        if pdf and structure.get('pages'):
            started = time.perf_counter()
            await job.stage('pages', len(structure['pages']), 'Chấm điểm OCR từng trang')
            # A review from the monitor changed page texts: keep them, whatever the file text is now.
            rows = await _page_rows(
                file,
                content,
                structure,
                settings['files.ocr.min_score'],
                keep_existing=rebuilt_hash == file.hash or bool(options.get('rebuild')),
            )
            job.stats['pages'] = len(rows)
            job.stats['low_pages'] = sum(row.status == 'low' for row in rows)
            job.stats['pages_ms'] = _ms(started)

        # 2. repair, with the summary of the parts whose pages are already final at the same time
        changed = 0
        if rows and settings['files.repair.enable'] and not options.get('rebuild'):
            started = time.perf_counter()
            targets = {row.page for row in _repair_targets(rows, settings, options.get('pages'), bool(options.get('force')))}
            early = None
            if wants_summary and targets:

                async def early_parts() -> None:
                    started_early = time.perf_counter()
                    try:
                        await _summarize(
                            job, file, structure, settings, ready=lambda pages: not targets & set(pages), parts_only=True
                        )
                    except Exception as error:
                        log.warning('File %s: early summary parts failed (%s)', file_id, error)
                    job.stats['summary_early_ms'] = _ms(started_early)

                early = asyncio.create_task(early_parts())
            try:
                changed = await _repair_pages(
                    job, file, rows, settings, options.get('pages'), bool(options.get('force')), engine
                )
            finally:
                if early:
                    await early
            job.stats['repaired_pages'] = changed
            job.stats['repair_ms'] = _ms(started)
            rows = await FilePages.list(file_id)

        # 3. rebuild
        if changed or options.get('pages') or options.get('rebuild'):
            await job.stage('rebuild', 0, 'Ghép lại văn bản từ các trang đã sửa')
            content, structure = await _rebuild(file, rows, settings, engine)
            file = await Files.get_file_by_id(file_id)
            job.stats['rebuilt_hash'] = file.hash
            job.stats['chars'] = len(content)
        elif rebuilt_hash and rebuilt_hash == file.hash:
            job.stats['rebuilt_hash'] = rebuilt_hash
        job.stats['unverified_pages'] = sum(row.status in ('low', 'unverified', 'failed') for row in rows)

        # Spreadsheets: sheet/column profile for the attachment block, so a model queries them right away.
        if file.path and is_spreadsheet(file.filename, (file.meta or {}).get('content_type')):
            try:
                from open_webui.file_pipeline.tables import profile_text, read_grid
                from open_webui.models.files import Files
                from open_webui.storage.provider import Storage

                local = await asyncio.to_thread(Storage.get_file, file.path)
                sheets = await asyncio.to_thread(read_grid, local, file.filename)
                await Files.update_file_data_by_id(file_id, {'table_profile': profile_text(sheets)})
            except Exception as error:
                log.warning('File %s: no table profile (%s)', file_id, error)

        # 4. index and 5. summary together: the index uses the embedding service, the summary the model.
        index_needed = bool(changed or options.get('pages') or options.get('rebuild') or settings['files.chunk.by_structure'])

        async def index_stage() -> None:
            started = time.perf_counter()
            job.stats['chunks'] = await _index(app, file, content, structure, settings)
            job.stats['index_ms'] = _ms(started)

        stages = []
        if index_needed:
            stages.append(index_stage())
        if wants_summary:
            stages.append(summary_stage(file, structure))
        if stages:
            label = ' + '.join(
                name for name, used in (('Lập chỉ mục theo Chương/Mục/Điều', index_needed), ('Tóm tắt sẵn', wants_summary)) if used
            )
            await job.stage('index' if index_needed else 'summary', 0, label)
            results = await asyncio.gather(*stages, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

        job.stats['total_ms'] = _ms(started_all)
        await FileJobs.finish(file_id, lease_id, 'done', job.stats)
        log.info('File pipeline done for %s (%s): %s', file.filename, file_id, job.stats)
    except Exception as error:
        log.exception('File pipeline failed for %s', file_id)
        job.stats['total_ms'] = _ms(started_all)
        await FileJobs.finish(file_id, lease_id, 'failed', job.stats, f'{type(error).__name__}: {error}')
