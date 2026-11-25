"""File summaries: one overview that covers every section, made part by part; details come from the text itself.

  overview  The file is cut into parts (fixed page ranges for PDFs, planned once per file). Each part is sent to
            the model on its own and comes back as its headings, in order, each with its pages and a few main
            points. The overview is these parts put together in document order, so no section can be left out by
            a merge; nothing is merged.
  details   A question about one section or some pages does not use a summary: the original text of those pages
            is given to the answering model, a page of it at a time when it is long (source_view).

Part results are kept on the file by their text, so a part is sent to the model once: the background pipeline
summarizes the parts whose pages are final while other pages are still being repaired, and a question uses the
parts already made. A file uploaded again (same bytes) takes the overview made for the first copy.
"""

import asyncio
import hashlib
import logging
import re
from typing import Awaitable, Callable, Optional

from open_webui.utils.file_structure import build_outline, pages_between, render_pdf_page, split_segments

log = logging.getLogger(__name__)

THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)

VERSION = 3

PART_PROMPT = """You are reading part {index} of {total} of the document "{name}"{pages}. Page starts are marked [trang N].
Write the overview of this part, in the language of the document. For every heading of this part (chapter, section,
article, requirement group...), in the order they appear, and for text that continues a section from the previous
part (start with its heading):

## <heading as written in the document> [tr.<first page>-<last page>]
- 2 to 5 main points: what the section is about, and its key numbers, dates, deadlines, amounts, thresholds,
  roles, obligations, conditions and exceptions, each with its page like [tr.12]

Write the heading's level with the number of #: ## for a top-level section (chapter, "2.13"), ### for its
subsections ("2.13.2"), #### below that. The pages of a heading are its own text, before its first subsection.
Cover every heading; do not leave one out. Keep it short (about {target} characters for this part): main points
only, no detail lists, no introduction, no conclusion. Do not add anything that is not in the text.{images}

<part>
{text}
</part>"""

Complete = Callable[[object], Awaitable[str]]

MAX_CACHED_PARTS = 400
_part_locks: dict = {}

_HEADING_RE = re.compile(r'^(#{2,6})\s+(.+?)\s*\[\s*tr\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*\]\s*$', re.MULTILINE)


def cache_key(content: str, file_hash: Optional[str], focus: Optional[str] = None) -> str:
    return hashlib.sha256(
        f'v{VERSION}:{len(content)}:{file_hash or ""}:{(focus or "").strip().lower()}'.encode()
    ).hexdigest()[:16]


def _usable(cached) -> bool:
    return isinstance(cached, dict) and cached.get('version') == VERSION and bool(cached.get('summary'))


def cached_summary(file, focus: Optional[str] = None) -> Optional[dict]:
    content = (file.data or {}).get('content') or ''
    cached = ((file.data or {}).get('summaries') or {}).get(cache_key(content, file.hash, focus))
    return cached if _usable(cached) else None


async def store_summary(file, focus: Optional[str], result: dict) -> None:
    from open_webui.models.files import Files

    content = (file.data or {}).get('content') or ''
    summaries = dict((file.data or {}).get('summaries') or {})
    summaries = dict(list(summaries.items())[-4:])
    summaries[cache_key(content, file.hash, focus)] = {**result, 'id': file.id, 'filename': file.filename}
    await Files.update_file_data_by_id(file.id, {'summaries': summaries})


async def reuse_summary(file) -> Optional[dict]:
    """The overview of another upload of the same file (same bytes, same extracted text), kept on this one."""
    from sqlalchemy import select

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File

    if not file.hash:
        return None
    key = cache_key((file.data or {}).get('content') or '', file.hash)
    async with get_async_db_context() as db:
        rows = (
            await db.execute(select(File.data).where(File.hash == file.hash, File.id != file.id).limit(5))
        ).all()
    for (data,) in rows:
        cached = ((data or {}).get('summaries') or {}).get(key)
        if _usable(cached):
            await store_summary(file, None, cached)
            return {**cached, 'id': file.id, 'filename': file.filename}
    return None


def part_key(text: str, pages: str) -> str:
    return hashlib.sha256(f'v{VERSION}|{pages}|{text}'.encode()).hexdigest()[:20]


def cached_parts(file) -> dict:
    return dict((file.data or {}).get('summary_parts') or {})


async def store_part(file_id: str, key: str, text: str) -> None:
    """Keep one part's overview on the file (the newest MAX_CACHED_PARTS)."""
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


def part_target(part_chars: int, ratio_pct: int, min_chars: int) -> int:
    """About how long one part's overview should be: a share of the part, at least min_chars."""
    return max(min_chars, part_chars * max(ratio_pct, 1) // 100)


def summary_ceiling(message) -> int:
    """max_tokens for one part's overview: far above the length asked for (about a tenth of the part), so it only
    stops a model that keeps repeating itself. Text runs at about 3 characters per token: a quarter of the
    characters is about three quarters of the part's own tokens."""
    text = message if isinstance(message, str) else ''.join(
        item.get('text', '') for item in message if isinstance(item, dict)
    )
    return max(2048, len(text) // 4)


def _page_label(pages: list[int]) -> str:
    if not pages:
        return ''
    return f'tr.{pages[0]}' if len(pages) == 1 else f'tr.{pages[0]}-{pages[-1]}'


def _blocks_of(part: dict) -> list[dict]:
    """A part's overview as its headings, each {'title', 'first', 'last', 'body', 'part'} (pages from the heading;
    a part that names no heading is one block of its own pages)."""
    text = part['text']
    matches = list(_HEADING_RE.finditer(text))
    blocks = []
    lead = text[: matches[0].start()].strip() if matches else text.strip()
    if lead:  # text before the first heading (or a part without headings): up to the first heading's page
        blocks.append({
            'title': part['label'],
            'level': 2,
            'first': part.get('first'),
            'last': int(matches[0].group(3)) if matches else part.get('last'),
            'body': lead,
            'lead': True,
        })
    for i, match in enumerate(matches):
        first, last = int(match.group(3)), int(match.group(4) or match.group(3))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append({
            'title': match.group(2).strip(),
            'level': len(match.group(1)),
            'first': min(first, last),
            'last': max(first, last),
            'body': text[match.end() : end].strip(),
        })
    for block in blocks:
        block['part'] = part['index']
    return blocks


def _join_blocks(parts: list[dict]) -> list[dict]:
    """The blocks of all parts in order; a section running on into the next part is one block, not two."""
    joined: list[dict] = []
    for part in parts:
        for block in _blocks_of(part):
            previous = joined[-1] if joined else None
            # Text at the top of a part with no heading of its own goes on the section before it.
            if block.get('lead') and previous and previous['last'] and part.get('first'):
                previous['last'] = max(previous['last'], block['last'] or 0)
                previous['body'] = f'{previous["body"]}\n{block["body"]}'.strip()
                continue
            if previous and previous['part'] != block['part'] and _normalize(previous['title']) == _normalize(block['title']):
                previous['last'] = max(previous['last'] or 0, block['last'] or 0) or None
                previous['body'] = f'{previous["body"]}\n{block["body"]}'.strip()
                previous['part'] = block['part']
                continue
            joined.append({k: v for k, v in block.items() if k != 'lead'})
    return joined


def _render(block: dict) -> str:
    marks = '#' * min(max(block.get('level') or 2, 2), 6)
    if block.get('first'):
        pages = f'tr.{block["first"]}' if block['first'] == block['last'] else f'tr.{block["first"]}-{block["last"]}'
        return f'{marks} {block["title"]} [{pages}]\n{block["body"]}'.strip()
    return f'{marks} {block["title"]}\n{block["body"]}'.strip()


async def summarize_file(
    file,
    structure: dict,
    complete: Complete,
    *,
    segment_chars: int,
    concurrency: int,
    ratio_pct: int = 8,
    min_chars: int = 1500,
    image_max_side: int = 1600,
    pdf_path: Optional[str] = None,
    image_pages: Optional[set] = None,
    emit: Optional[Callable[[str, bool], Awaitable[None]]] = None,
    plan: Optional[list] = None,
    part_cache: Optional[dict] = None,
    save_part: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ready: Optional[Callable[[list], bool]] = None,
    model_calls: bool = True,
) -> dict:
    """The overview of the whole file text (see the module docstring).

    image_pages are pages whose text is unreliable; with pdf_path their images go with their part (2 at most).
    plan: page ranges from ensure_plan (paged files). part_cache / save_part: part overviews made before / to keep.
    ready(pages): whether a part's pages are final (a part that is not is left for later).
    model_calls=False: only the parts already made (nothing is sent to the model).
    A result missing parts has complete=False and is not cached as the file's overview.
    """
    content = (file.data or {}).get('content') or ''
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
                    '\n'.join(f'[trang {n}]\n' + content[offsets[n][0]:offsets[n][1]] for n in numbers),
                ))
    else:
        segments = [
            (start, end, pages_between(structure, start, end), content[start:end])
            for start, end in await asyncio.to_thread(split_segments, content, structure, segment_chars)
        ]
    total = len(segments)
    part_cache = part_cache if part_cache is not None else {}
    image_pages = image_pages or set()
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    done = 0

    async def notify(text: str, finished: bool = False):
        if emit:
            await emit(text, finished)

    async def part(index: int, start: int, end: int, pages: list, text: str) -> dict:
        nonlocal done
        entry = {
            'index': index,
            'label': f'Phần {index}/{total}',
            'pages': _page_label(pages),
            'first': pages[0] if pages else None,
            'last': pages[-1] if pages else None,
            'start': start,
            'end': end,
        }
        key = part_key(text, _page_label(pages))
        if part_cache.get(key):
            done += 1
            return {**entry, 'text': part_cache[key]}
        if not model_calls or (ready and not ready(pages)):
            return {**entry, 'pending': True}
        with_images = [page for page in pages if page in image_pages][:2] if pdf_path else []
        prompt = PART_PROMPT.format(
            index=index,
            total=total,
            name=file.filename,
            pages=f' (pages {pages[0]}-{pages[-1]})' if len(pages) > 1 else (f' (page {pages[0]})' if pages else ''),
            target=part_target(len(text), ratio_pct, min_chars),
            images=(
                f'\nThe OCR text of page(s) {", ".join(map(str, with_images))} is unreliable; the page images are '
                'attached, read those pages from the images.'
                if with_images
                else ''
            ),
            text=text,
        )
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
        async with semaphore:
            summary = THINK_RE.sub('', await complete(message) or '').strip()
        if summary:
            part_cache[key] = summary
            if save_part:
                try:
                    await save_part(key, summary)
                except Exception as error:
                    log.warning('summary: part of %s not saved (%s)', file.id, error)
        done += 1
        await notify(f'Đã đọc {done}/{total} phần của {file.filename}')
        return {**entry, 'text': summary or '(không có nội dung tóm tắt)'}

    await notify(f'Đang đọc {file.filename} ({total} phần)')
    parts = list(await asyncio.gather(*(part(i + 1, *segment) for i, segment in enumerate(segments))))
    made = [p for p in parts if not p.get('pending')]
    pending = [p for p in parts if p.get('pending')]
    blocks = _join_blocks(made)
    result = {
        'version': VERSION,
        'id': file.id,
        'filename': file.filename,
        **({'pages': len(structure['pages'])} if structure.get('pages') else {}),
        'parts_read': len(made),
        'parts_total': total,
        # Overview in document order, one block per heading.
        'summary': '\n\n'.join(_render(block) for block in blocks),
        'sections': [{k: block.get(k) for k in ('title', 'level', 'first', 'last', 'part')} for block in blocks],
        'parts': [{k: p[k] for k in ('index', 'label', 'pages', 'first', 'last', 'start', 'end')} for p in parts],
    }
    if pending:
        result.update(complete=False, pending_pages=[p['pages'] or p['label'] for p in pending])
    return result


def _normalize(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize('NFD', (text or '').lower())
    text = ''.join(c for c in text if not unicodedata.combining(c)).replace('đ', 'd')
    # Words only: "HỘI - NHÓM", "hội nhóm" and "Hội-nhóm" are the same heading.
    return ' '.join(re.sub(r'[^\w]+', ' ', text).split())


def parse_pages(spec: str, last_page: int) -> list[int]:
    """Pages named by "12", "12-18" or "3, 12-18" (within 1..last_page), in order, once each."""
    pages: list[int] = []
    for first, last in re.findall(r'(\d+)\s*(?:[-–]\s*(\d+))?', spec or ''):
        low, high = int(first), int(last or first)
        for page in range(min(low, high), max(low, high) + 1):
            if 1 <= page <= last_page and page not in pages:
                pages.append(page)
    return pages


def find_sections(overview: Optional[dict], outline: dict, wanted: str) -> list[dict]:
    """Sections whose title contains what was asked for (or is contained in it), from the overview's headings and
    the file's own outline; each {'title', 'first', 'last'} (pages) or {'title', 'start', 'end'} (text offsets)."""
    key = _normalize(wanted)
    if not key:
        return []

    def matches(title: str) -> bool:
        name = _normalize(title)
        return bool(name) and (key in name or name in key)

    # A section is its heading and its subsections (the headings after it at a deeper level), so "2.13 Quản lý
    # hội - nhóm" brings 2.13.2 and 2.13.3 with it.
    sections = (overview or {}).get('sections') or []
    hits = []
    for i, section in enumerate(sections):
        if not (section.get('first') and matches(section['title'])):
            continue
        level, last = section.get('level') or 2, section['last']
        for child in sections[i + 1 :]:
            if (child.get('level') or 2) <= level:
                break
            last = max(last, child.get('last') or last)
        hits.append({**section, 'last': last})
    if hits:
        return hits
    headings = outline.get('headings') or []
    found = []
    for i, heading in enumerate(headings):
        if not matches(heading['title']):
            continue
        following = [h for h in headings[i + 1 :] if h['level'] <= heading['level']]
        end = following[0]['offset'] if following else outline.get('total_chars', heading['offset'])
        found.append({'title': heading['title'], 'start': heading['offset'], 'end': end})
    if not found:  # a file without pages: the overview's sections point at their part's text
        for section in (overview or {}).get('sections') or []:
            part = next((p for p in (overview or {}).get('parts') or [] if p['index'] == section.get('part')), None)
            if part and matches(section['title']):
                found.append({'title': section['title'], 'start': part['start'], 'end': part['end']})
    return found


def rank_regions(regions: list[dict], hits: list[int]) -> list[dict]:
    """Regions ({'lo', 'hi', ...}: page or offset bounds) scored by the search hits that fall in them.

    hits are positions (pages or offsets) in rank order, best first; a hit counts for the narrowest region that
    holds it (a subsection rather than its chapter), weighted by its rank. The weight falls slowly (1/sqrt): a
    section read for nothing costs one reading step, which drops it as unrelated; a section missed loses part of
    the answer. Regions without a hit are left out.
    """
    scores: dict[int, float] = {}
    for rank, position in enumerate(hits):
        holding = [i for i, region in enumerate(regions) if region['lo'] <= position <= region['hi']]
        if holding:
            narrowest = min(holding, key=lambda i: regions[i]['hi'] - regions[i]['lo'])
            scores[narrowest] = scores.get(narrowest, 0.0) + 1.0 / (rank + 1) ** 0.5
    return [{**regions[i], 'score': score} for i, score in sorted(scores.items(), key=lambda item: -item[1])]


def with_parents(picked: list[dict], regions: list[dict]) -> list[dict]:
    """Picked subsections bring their parent section's own text (its introduction before its first subsection):
    regions are in document order, each with a 'level'; the parent is the nearest earlier one at a lower level."""
    out = list(picked)
    keys = {(r['lo'], r['hi']) for r in picked}
    for region in picked:
        level = region.get('level') or 2
        index = next((i for i, r in enumerate(regions) if (r['lo'], r['hi']) == (region['lo'], region['hi'])), None)
        if index is None:
            continue
        parent = next((r for r in reversed(regions[:index]) if (r.get('level') or 2) < level), None)
        if parent and (parent['lo'], parent['hi']) not in keys:
            keys.add((parent['lo'], parent['hi']))
            out.append({**parent, 'score': region.get('score', 0)})
    return sorted(out, key=lambda r: r['lo'])


def pick_regions(ranked: list[dict], size_of: Callable[[dict], int], max_size: int, share: float = 0.3) -> list[dict]:
    """The regions worth reading: those scoring at least `share` of the best one, best first while they fit in
    max_size (size_of a region), returned in document order."""
    if not ranked:
        return []
    best = ranked[0]['score']
    picked, used, seen = [], 0, set()
    for region in ranked:
        if region['score'] < best * share:
            break
        if (region['lo'], region['hi']) in seen:  # found both by its name and by meaning
            continue
        seen.add((region['lo'], region['hi']))
        size = size_of(region)
        if picked and used + size > max_size:
            continue
        picked.append(region)
        used += size
    return sorted(picked, key=lambda region: region['lo'])


EXTRACT_PROMPT = """The user asked about the document "{name}": {question}
Below is the original text of {where} (page starts are marked [trang N]). Write down everything in it that answers or
bears on the question, in the language of the document: every relevant feature, screen, field, button, rule,
condition, permission, number and exception, with its page like [tr.12], in document order. Keep the details; leave
out only what has nothing to do with the question. If nothing here is relevant, write only: KHÔNG LIÊN QUAN

<text>
{text}
</text>"""

COMBINE_PROMPT = """Below are notes taken, in order, from parts of the document "{name}" for the question: {question}
Combine them into one set of notes in document order: keep every relevant item with its page [tr.N]; remove only
repetition.

{text}"""

NOTHING = 'KHÔNG LIÊN QUAN'


async def extract_for_question(
    name: str,
    question: str,
    blocks: list[str],
    complete: Complete,
    *,
    chunk_chars: int,
    fit_chars: int,
    concurrency: int,
    emit: Optional[Callable[[str, bool], Awaitable[None]]] = None,
) -> str:
    """Notes answering `question` from source text too long to give the answering model at once.

    The text is cut into chunks of chunk_chars, each read by the model on its own (in parallel) for what bears on
    the question; the notes are put together in order and, while longer than fit_chars, combined again. Nothing
    is left for the user to ask for: the whole text is read within the answer.
    """
    chunks = paginate(blocks, chunk_chars)
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    done = 0

    def where(chunk: str) -> str:
        pages = re.findall(r'^\[trang (\d+)\]', chunk, re.MULTILINE)
        return f'pages {pages[0]}-{pages[-1]}' if pages else 'a part of it'

    async def read(chunk: str) -> str:
        nonlocal done
        async with semaphore:
            note = THINK_RE.sub('', await complete(
                EXTRACT_PROMPT.format(name=name, question=question, where=where(chunk), text=chunk)
            ) or '').strip()
        done += 1
        if emit:
            await emit(f'Đã đọc {done}/{len(chunks)} đoạn liên quan của {name}', False)
        return '' if not note or note.upper().startswith(NOTHING) else note

    notes = [note for note in await asyncio.gather(*(read(chunk) for chunk in chunks)) if note]
    for _ in range(3):
        joined = '\n\n'.join(notes)
        if len(joined) <= fit_chars or len(notes) <= 1:
            return joined
        groups = paginate(notes, chunk_chars)
        if emit:
            await emit(f'Đang gộp ghi chú của {name}', False)

        async def combine(group: str) -> str:
            async with semaphore:
                return THINK_RE.sub('', await complete(
                    COMBINE_PROMPT.format(name=name, question=question, text=group)
                ) or '').strip()

        notes = [note for note in await asyncio.gather(*(combine(group) for group in groups)) if note]
    return '\n\n'.join(notes)


def source_view(content: str, structure: dict, pages: list[int], ranges: list[tuple[int, int]]) -> list[str]:
    """The original text of these pages (each marked [trang N]) or text ranges, in document order."""
    if pages:
        offsets = {page: (start, end) for page, start, end in structure.get('pages') or []}
        return [f'[trang {page}]\n{content[offsets[page][0]:offsets[page][1]].strip()}' for page in pages if page in offsets]
    blocks = []
    for start, end in sorted(set(ranges)):
        if blocks and start < blocks[-1][1]:  # overlapping ranges are read once
            start = blocks[-1][1]
        if start < end:
            blocks.append((start, end))
    return [content[start:end].strip() for start, end in blocks]


def overview_blocks(result: dict) -> list[str]:
    """The overview cut at its top-level headings, so a long one is delivered in whole sections."""
    text = result.get('summary') or ''
    starts = [m.start() for m in re.finditer(r'^## ', text, re.MULTILINE)]
    if not starts or starts[0] != 0:
        starts = [0, *starts]
    return [text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)]) if text[a:b].strip()]


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


__all__ = [
    'build_outline',
    'cache_key',
    'cached_parts',
    'cached_summary',
    'ensure_plan',
    'extract_for_question',
    'find_sections',
    'pick_regions',
    'rank_regions',
    'with_parents',
    'overview_blocks',
    'paginate',
    'parse_pages',
    'reuse_summary',
    'source_view',
    'store_part',
    'store_summary',
    'summarize_file',
    'summary_ceiling',
]
