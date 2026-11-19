"""Whole-file summaries by map-reduce, shared by the summarize_chat_file tool and the background pipeline.

The file is cut at section, sheet and page starts; each part is summarized on its own (with the page images of
parts whose OCR is still unreliable). Nothing is thrown away: three levels are kept,

  parts     one summary per part (most detail)
  sections  one per top-level section (chapter...), merged from its parts only when they are long
  overall   the whole file, merged until it fits a length that grows with the file

so a question picks the level it needs ("tóm tắt ngắn", "đầy đủ", "Chương II") and long results are delivered
part by part. Results are cached on the file by content and focus, so the chat tool returns what the pipeline
already made.

Each part's summary is also kept on its own (by the part's text), so a part is sent to the model once: the
background pipeline summarizes the parts whose pages are final while other pages are still being repaired, and
an answer that needs the summary uses the parts already made. A PDF is cut into fixed page ranges (planned once
per file), so repairing one page only changes the part that holds it.
"""

import asyncio
import hashlib
import logging
import re
import time
from typing import Awaitable, Callable, Optional

from open_webui.utils.file_structure import build_outline, pages_between, render_pdf_page, split_segments

log = logging.getLogger(__name__)

THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)

PART_PROMPT = """You are reading part {index} of {total} of the document "{name}"{pages}{section}.
Summarize this part completely, in the language of the document, as bullet points in the order the content appears.
Keep every heading (part, chapter, section, article, clause), every number, date, deadline, amount, threshold,
person, organization, obligation, condition and exception, and write the page number in brackets after each point,
like [tr.12]. Do not add anything that is not in the text and do not comment on missing context.{focus}{images}

<part>
{text}
</part>"""

MERGE_PROMPT = """Below are consecutive partial summaries of the document "{name}"{scope}.
Merge them into one summary of at most about {target} characters, in the language of the document, keeping the
document order, every heading, number, date, deadline, obligation and exception, and the page references like
[tr.12]; only remove repetition and shorten wording.{focus}

{text}"""

Complete = Callable[[object], Awaitable[str]]

MAX_CACHED_PARTS = 400
_part_locks: dict = {}
# Part summaries still running after an answer stopped waiting: they finish and are kept for the next one.
_detached: set = set()


def cache_key(content: str, file_hash: Optional[str], focus: Optional[str]) -> str:
    return hashlib.sha256(f'{len(content)}:{file_hash or ""}:{(focus or "").strip().lower()}'.encode()).hexdigest()[:16]


def cached_summary(file, focus: Optional[str]) -> Optional[dict]:
    content = (file.data or {}).get('content') or ''
    cached = ((file.data or {}).get('summaries') or {}).get(cache_key(content, file.hash, focus))
    # Summaries stored before the three levels existed are made again, so every level can be asked for.
    return cached if isinstance(cached, dict) and cached.get('summary') and cached.get('tiers') else None


async def store_summary(file, focus: Optional[str], result: dict) -> None:
    from open_webui.models.files import Files

    content = (file.data or {}).get('content') or ''
    summaries = dict((file.data or {}).get('summaries') or {})
    summaries = dict(list(summaries.items())[-4:])
    summaries[cache_key(content, file.hash, focus)] = result
    await Files.update_file_data_by_id(file.id, {'summaries': summaries})


def part_key(text: str, pages: str, focus: Optional[str]) -> str:
    return hashlib.sha256(f'{(focus or "").strip().lower()}|{pages}|{text}'.encode()).hexdigest()[:20]


def cached_parts(file) -> dict:
    return dict((file.data or {}).get('summary_parts') or {})


async def store_part(file_id: str, key: str, text: str) -> None:
    """Keep one part's summary on the file (the newest MAX_CACHED_PARTS)."""
    from open_webui.models.files import Files

    lock = _part_locks.setdefault(file_id, asyncio.Lock())
    async with lock:
        file = await Files.get_file_by_id(file_id)
        if not file:
            return
        parts = cached_parts(file)
        parts.pop(key, None)
        parts[key] = text
        if len(parts) > MAX_CACHED_PARTS:
            parts = dict(list(parts.items())[-MAX_CACHED_PARTS:])
        await Files.update_file_data_by_id(file_id, {'summary_parts': parts})


def _plan_ranges(pages: list, segment_chars: int) -> list:
    """Consecutive page ranges of about segment_chars each (a longer page is a range of its own)."""
    ranges, current, size = [], [], 0
    for page, start, end in pages:
        if current and size + (end - start) > segment_chars:
            ranges.append([current[0], current[-1]])
            current, size = [], 0
        current.append(page)
        size += end - start
    if current:
        ranges.append([current[0], current[-1]])
    return ranges


async def ensure_plan(file, structure: dict, segment_chars: int) -> Optional[list]:
    """The file's fixed page ranges for summaries (None for files without pages), planned once and kept."""
    from open_webui.models.files import Files

    pages = structure.get('pages') or []
    if not pages:
        return None
    numbers = [page for page, _, _ in pages]
    stored = (file.data or {}).get('summary_plan') or {}
    if stored.get('segment_chars') == segment_chars and stored.get('pages') == numbers and stored.get('ranges'):
        return stored['ranges']
    ranges = _plan_ranges(pages, segment_chars)
    await Files.update_file_data_by_id(
        file.id, {'summary_plan': {'segment_chars': segment_chars, 'pages': numbers, 'ranges': ranges}}
    )
    return ranges


def target_chars(content_chars: int, ratio_pct: int, min_chars: int, max_chars: int) -> int:
    """Length of the whole-file summary: a share of the file, within bounds."""
    return max(min_chars, min(max_chars, content_chars * max(ratio_pct, 1) // 100))


def _page_label(pages: list[int]) -> str:
    if not pages:
        return ''
    return f'tr.{pages[0]}' if len(pages) == 1 else f'tr.{pages[0]}-{pages[-1]}'


def _sections(outline: dict, length: int, segments: list[tuple[int, int]]) -> list[tuple[str, int, int]]:
    """(title, start, end) of top-level sections; without headings, groups of about four parts."""
    headings = outline.get('headings') or []
    if headings:
        top = min(h['level'] for h in headings)
        starts = [(h['title'], h['offset']) for h in headings if h['level'] == top]
        if len(starts) >= 2:
            if starts[0][1] > 0:
                starts.insert(0, ('Phần đầu', 0))
            return [
                (title, start, starts[i + 1][1] if i + 1 < len(starts) else length)
                for i, (title, start) in enumerate(starts)
            ]
    groups = [segments[i : i + 4] for i in range(0, len(segments), 4)]
    return [(f'Phần {i + 1}', group[0][0], group[-1][1]) for i, group in enumerate(groups)]


async def summarize_file(
    file,
    structure: dict,
    complete: Complete,
    *,
    focus: Optional[str] = None,
    segment_chars: int,
    concurrency: int,
    max_chars: int,
    ratio_pct: int = 8,
    min_chars: int = 3000,
    image_max_side: int = 1600,
    pdf_path: Optional[str] = None,
    image_pages: Optional[set] = None,
    emit: Optional[Callable[[str, bool], Awaitable[None]]] = None,
    plan: Optional[list] = None,
    part_cache: Optional[dict] = None,
    save_part: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ready: Optional[Callable[[list], bool]] = None,
    deadline: Optional[float] = None,
    parts_only: bool = False,
    detach: bool = False,
) -> dict:
    """Three-level summary of the whole file text: see the module docstring for the levels.

    image_pages are pages whose text is unreliable; with pdf_path their images go with their part (2 at most).
    plan: page ranges from ensure_plan (paged files). part_cache / save_part: part summaries made before / to keep.
    ready(pages): whether a part's pages are final (a part that is not is left for later).
    deadline (time.monotonic()): parts not done by then are left out; with detach they keep running and are
    saved for the next call. parts_only: stop after the parts (no merging).
    A result missing parts or merging has complete=False and is not a whole-file summary to cache.
    """
    content = (file.data or {}).get('content') or ''
    outline = await asyncio.to_thread(build_outline, content, structure, 60)
    if plan and structure.get('pages'):
        offsets = {page: (start, end) for page, start, end in structure['pages']}
        segments = []
        for first, last in plan:
            numbers = [page for page in range(first, last + 1) if page in offsets]
            if numbers:
                segments.append((
                    offsets[numbers[0]][0],
                    offsets[numbers[-1]][1],
                    numbers,
                    '\n'.join(content[offsets[n][0]:offsets[n][1]] for n in numbers),
                ))
    else:
        segments = [
            (start, end, pages_between(structure, start, end), content[start:end])
            for start, end in await asyncio.to_thread(split_segments, content, structure, segment_chars)
        ]
    total = len(segments)
    part_cache = part_cache if part_cache is not None else {}
    image_pages = image_pages or set()
    focus_line = f'\nPay special attention to: {focus}' if focus else ''
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    overall_target = target_chars(len(content), ratio_pct, min_chars, max_chars)
    done = 0

    async def notify(text: str, finished: bool = False):
        if emit:
            await emit(text, finished)

    async def ask(message) -> str:
        async with semaphore:
            return THINK_RE.sub('', await complete(message) or '').strip()

    async def part(index: int, start: int, end: int, pages: list, text: str) -> dict:
        nonlocal done
        page_label = f' (pages {pages[0]}-{pages[-1]})' if len(pages) > 1 else (f' (page {pages[0]})' if pages else '')
        titles = [h['title'] for h in outline.get('headings', []) if start <= h['offset'] < end and h['level'] <= 2]
        section = f', covering {titles[0]}' + (f' … {titles[-1]}' if len(titles) > 1 else '') if titles else ''
        with_images = [page for page in pages if page in image_pages][:2] if pdf_path else []
        prompt = PART_PROMPT.format(
            index=index,
            total=total,
            name=file.filename,
            pages=page_label,
            section=section,
            focus=focus_line,
            images=(
                f'\nThe OCR text of page(s) {", ".join(map(str, with_images))} is unreliable; the page images are '
                'attached, read those pages from the images.'
                if with_images
                else ''
            ),
            text=text,
        )
        key = part_key(text, _page_label(pages), focus)
        entry = {
            'label': f'Phần {index}/{total}' + (f' – {titles[0]}' if titles else ''),
            'pages': _page_label(pages),
            'start': start,
        }
        if part_cache.get(key):
            done += 1
            return {**entry, 'text': part_cache[key]}
        if (ready and not ready(pages)) or (deadline is not None and time.monotonic() >= deadline):
            return {**entry, 'pending': True}
        message = prompt
        if with_images:
            images = []
            for page in with_images:
                try:
                    url, _ = await asyncio.to_thread(render_pdf_page, pdf_path, page, image_max_side)
                    images.append({'type': 'image_url', 'image_url': {'url': url}})
                except Exception as error:
                    log.warning('summary: page %s of %s not rendered (%s)', page, file.id, error)
            message = [{'type': 'text', 'text': prompt}, *images]

        async def run() -> str:
            nonlocal done
            summary = await ask(message)
            if summary:
                part_cache[key] = summary
                if save_part:
                    try:
                        await save_part(key, summary)
                    except Exception as error:
                        log.warning('summary: part of %s not saved (%s)', file.id, error)
            done += 1
            await notify(f'Đã đọc {done}/{total} phần của {file.filename}')
            return summary

        if deadline is None:
            summary = await run()
        else:
            task = asyncio.create_task(run())
            finished, _ = await asyncio.wait({task}, timeout=max(deadline - time.monotonic(), 0))
            if not finished:
                if detach:
                    _detached.add(task)
                    task.add_done_callback(_detached.discard)
                else:
                    task.cancel()
                return {**entry, 'pending': True, 'task': task}
            summary = task.result()
        return {**entry, 'text': summary or '(no summary returned)'}

    async def merge(texts: list[str], target: int, scope: str) -> str:
        """Merge texts in document order, in groups that fit one call, until the result fits target."""
        rounds = 0
        while (len(texts) > 1 or (texts and len(texts[0]) > target * 1.3)) and rounds < 6:
            rounds += 1
            groups, current = [], []
            for text in texts:
                if current and sum(len(t) for t in current) + len(text) > segment_chars:
                    groups.append(current)
                    current = []
                current.append(text)
            groups.append(current)
            if len(groups) == len(texts) and len(texts) > 1:
                groups = [texts[i : i + 2] for i in range(0, len(texts), 2)]
            share = max(target // max(len(groups), 1), 1500)
            merged = await asyncio.gather(
                *(
                    ask(MERGE_PROMPT.format(name=file.filename, scope=scope, target=share, focus=focus_line, text='\n\n'.join(group)))
                    if len(group) > 1 or len(group[0]) > target * 1.3
                    else asyncio.sleep(0, group[0])
                    for group in groups
                )
            )
            if len(merged) == len(texts) and all(a == b for a, b in zip(merged, texts)):
                break
            texts = list(merged)
            if len(texts) == 1:
                break
        return texts[0] if texts else ''

    await notify(f'Đang đọc {file.filename} ({total} phần)')
    parts = list(await asyncio.gather(*(part(i + 1, *segment) for i, segment in enumerate(segments))))
    pending = [p for p in parts if p.get('pending')]
    parts_done = [p for p in parts if not p.get('pending')]

    def partial() -> dict:
        return {
            'id': file.id,
            'filename': file.filename,
            'complete': False,
            'parts_read': len(parts_done),
            'parts_total': total,
            'pending_pages': [p['pages'] or p['label'] for p in pending],
            'running': [p['task'] for p in pending if p.get('task')],
            **({'pages': len(structure['pages'])} if structure.get('pages') else {}),
            'coverage': [
                item['title'] + (f' (tr.{item["page"]})' if item.get('page') else '')
                for item in outline.get('headings', [])
            ],
            **({'focus': focus} if focus else {}),
            'summary': '\n\n'.join(_block('###', p['label'], p['pages'], p['text']) for p in parts_done),
            'tiers': {
                'parts': [{k: p[k] for k in ('label', 'pages', 'text')} for p in parts_done],
                'sections': [],
                'overall': '',
            },
        }

    if pending or parts_only:
        return partial()

    async def finish() -> dict:
        # Sections: their parts as they are when short, merged when long.
        section_ranges = _sections(outline, len(content), segments)
        section_cap = max(4000, overall_target * 2 // max(len(section_ranges), 1))
        sections = []
        await notify(f'Đang gộp tóm tắt {file.filename}')
        for title, start, end in section_ranges:
            members = [p for p in parts if start <= p['start'] < end]
            if not members:
                continue
            texts = [f'### {p["label"]} ({p["pages"]})\n{p["text"]}' if p['pages'] else f'### {p["label"]}\n{p["text"]}' for p in members]
            joined = '\n\n'.join(texts)
            pages = [p['pages'] for p in members if p['pages']]
            page_range = ''
            if pages:
                first = pages[0].removeprefix('tr.').split('-')[0]
                last = pages[-1].removeprefix('tr.').split('-')[-1]
                page_range = f'tr.{first}' if first == last else f'tr.{first}-{last}'
            text = joined if len(joined) <= section_cap else await merge(texts, section_cap, f', section "{title}"')
            sections.append({'title': title, 'pages': page_range, 'text': text})

        section_texts = [f'## {s["title"]}' + (f' ({s["pages"]})' if s['pages'] else '') + f'\n{s["text"]}' for s in sections]
        joined = '\n\n'.join(section_texts)
        overall = joined if len(joined) <= overall_target * 1.3 else await merge(section_texts, overall_target, '')

        return {
            'id': file.id,
            'filename': file.filename,
            'parts_read': total,
            **({'pages': len(structure['pages'])} if structure.get('pages') else {}),
            'coverage': [
                item['title'] + (f' (tr.{item["page"]})' if item.get('page') else '') for item in outline.get('headings', [])
            ],
            **({'focus': focus} if focus else {}),
            'summary': overall,
            'tiers': {
                'parts': [{k: p[k] for k in ('label', 'pages', 'text')} for p in parts],
                'sections': sections,
                'overall': overall,
            },
        }

    if deadline is None:
        return await finish()
    try:
        return await asyncio.wait_for(finish(), timeout=max(deadline - time.monotonic(), 1))
    except asyncio.TimeoutError:
        return partial()


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize('NFD', (text or '').lower())
    return ''.join(c for c in text if not unicodedata.combining(c)).replace('đ', 'd')


def select_view(result: dict, detail: str = 'normal', section: Optional[str] = None) -> list[str]:
    """Blocks of the summary for a detail level (brief | normal | full), optionally one section."""
    tiers = result.get('tiers') or {}
    if not tiers:  # summaries cached before levels existed
        return [result.get('summary') or '']
    detail = (detail or 'normal').strip().lower()
    if not tiers.get('sections'):  # partial result: only parts so far
        detail = 'full'
    if section:
        key = _normalize(section)
        page = re.search(r'(\d+)', key) if re.search(r'\b(tr|trang|page)\b', key) else None
        if page:
            number = int(page.group(1))

            def covers(pages: str) -> bool:
                bounds = [int(x) for x in re.findall(r'\d+', pages or '')]
                return bool(bounds) and bounds[0] <= number <= bounds[-1]

            hits = [p for p in tiers['parts'] if covers(p['pages'])]
        else:
            hits = [p for p in tiers['parts'] if key in _normalize(p['label'])]
            matching_sections = [s for s in tiers.get('sections') or [] if key in _normalize(s['title'])]
            if matching_sections and detail != 'full':
                return [_block('##', s['title'], s['pages'], s['text']) for s in matching_sections]
        if hits:
            return [_block('###', p['label'], p['pages'], p['text']) for p in hits]
        names = [s['title'] for s in tiers.get('sections') or []] or [p['label'] for p in tiers['parts']]
        return [f'(Không tìm thấy phần "{section}"; các phần: ' + '; '.join(names) + ')']
    if detail in ('brief', 'short', 'ngan'):
        return [tiers['overall']]
    if detail in ('full', 'day_du', 'detail'):
        return [_block('###', p['label'], p['pages'], p['text']) for p in tiers['parts']]
    return [_block('##', s['title'], s['pages'], s['text']) for s in tiers['sections']]


def _block(level: str, title: str, pages: str, text: str) -> str:
    return f'{level} {title}' + (f' ({pages})' if pages else '') + f'\n{text}'


def paginate(blocks: list[str], page_chars: int) -> list[str]:
    """Group blocks into pages of about page_chars (a block longer than a page is cut on line breaks)."""
    pages, current = [], ''
    for block in blocks:
        while len(block) > page_chars:
            cut = block.rfind('\n', 0, page_chars)
            cut = cut if cut > page_chars // 2 else page_chars
            if current:
                pages.append(current)
                current = ''
            pages.append(block[:cut])
            block = block[cut:].lstrip('\n')
        if current and len(current) + len(block) + 2 > page_chars:
            pages.append(current)
            current = ''
        current = f'{current}\n\n{block}' if current else block
    if current:
        pages.append(current)
    return pages or ['']
