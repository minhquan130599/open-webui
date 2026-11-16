"""Whole-file summaries by map-reduce, shared by the summarize_chat_file tool and the background pipeline.

The file is cut at section, sheet and page starts; each part is summarized on its own (with the page images of
parts whose OCR is still unreliable), then neighbouring summaries are merged until the result fits. Results are
cached on the file by content and focus, so the chat tool returns a summary the pipeline already made.
"""

import asyncio
import hashlib
import logging
import re
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

MERGE_PROMPT = """Below are consecutive partial summaries of the document "{name}".
Merge them into one summary in the language of the document, keeping the document order, every heading, number,
date, deadline, obligation and exception, and the page references like [tr.12]; only remove repetition and
shorten wording.{focus}

{text}"""

Complete = Callable[[object], Awaitable[str]]


def cache_key(content: str, file_hash: Optional[str], focus: Optional[str]) -> str:
    return hashlib.sha256(f'{len(content)}:{file_hash or ""}:{(focus or "").strip().lower()}'.encode()).hexdigest()[:16]


def cached_summary(file, focus: Optional[str]) -> Optional[dict]:
    content = (file.data or {}).get('content') or ''
    cached = ((file.data or {}).get('summaries') or {}).get(cache_key(content, file.hash, focus))
    return cached if isinstance(cached, dict) and cached.get('summary') else None


async def store_summary(file, focus: Optional[str], result: dict) -> None:
    from open_webui.models.files import Files

    content = (file.data or {}).get('content') or ''
    summaries = dict((file.data or {}).get('summaries') or {})
    summaries = dict(list(summaries.items())[-4:])
    summaries[cache_key(content, file.hash, focus)] = result
    await Files.update_file_data_by_id(file.id, {'summaries': summaries})


def _section_label(outline: dict, start: int, end: int) -> str:
    titles = [item['title'] for item in outline.get('headings', []) if start <= item['offset'] < end and item['level'] <= 2]
    if not titles:
        return ''
    return f', covering {titles[0]}' + (f' … {titles[-1]}' if len(titles) > 1 else '')


async def summarize_file(
    file,
    structure: dict,
    complete: Complete,
    *,
    focus: Optional[str] = None,
    segment_chars: int,
    concurrency: int,
    max_chars: int,
    image_max_side: int = 1600,
    pdf_path: Optional[str] = None,
    image_pages: Optional[set] = None,
    emit: Optional[Callable[[str, bool], Awaitable[None]]] = None,
) -> dict:
    """Summary of the whole file text: {'summary', 'coverage', 'parts_read', 'pages'?}.

    image_pages are pages whose text is unreliable; with pdf_path their images go with their part (2 at most).
    """
    content = (file.data or {}).get('content') or ''
    outline = await asyncio.to_thread(build_outline, content, structure, 60)
    segments = await asyncio.to_thread(split_segments, content, structure, segment_chars)
    total = len(segments)
    image_pages = image_pages or set()
    focus_line = f'\nPay special attention to: {focus}' if focus else ''
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    done = 0

    async def notify(text: str, finished: bool = False):
        if emit:
            await emit(text, finished)

    async def part(index: int, start: int, end: int) -> str:
        nonlocal done
        pages = pages_between(structure, start, end)
        page_label = f' (pages {pages[0]}-{pages[-1]})' if len(pages) > 1 else (f' (page {pages[0]})' if pages else '')
        with_images = [page for page in pages if page in image_pages][:2] if pdf_path else []
        prompt = PART_PROMPT.format(
            index=index,
            total=total,
            name=file.filename,
            pages=page_label,
            section=_section_label(outline, start, end),
            focus=focus_line,
            images=(
                f'\nThe OCR text of page(s) {", ".join(map(str, with_images))} is unreliable; the page images are '
                'attached, read those pages from the images.'
                if with_images
                else ''
            ),
            text=content[start:end],
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
        done += 1
        await notify(f'Đã đọc {done}/{total} phần của {file.filename}')
        return f'### Part {index}/{total}{page_label}\n{summary or "(no summary returned)"}'

    await notify(f'Đang đọc {file.filename} ({total} phần)')
    parts = list(await asyncio.gather(*(part(i + 1, s, e) for i, (s, e) in enumerate(segments))))

    # Merge neighbouring summaries until the result fits, keeping document order.
    while sum(len(p) for p in parts) > max_chars and len(parts) > 1:
        groups, current = [], []
        for item in parts:
            if current and sum(len(p) for p in current) + len(item) > segment_chars:
                groups.append(current)
                current = []
            current.append(item)
        groups.append(current)
        if len(groups) == len(parts):
            groups = [parts[i : i + 2] for i in range(0, len(parts), 2)]
        await notify(f'Đang gộp tóm tắt {file.filename}')

        async def merge(group: list[str]) -> str:
            if len(group) == 1:
                return group[0]
            async with semaphore:
                merged = await complete(MERGE_PROMPT.format(name=file.filename, focus=focus_line, text='\n\n'.join(group)))
            return THINK_RE.sub('', merged or '').strip()

        parts = list(await asyncio.gather(*(merge(group) for group in groups)))

    return {
        'id': file.id,
        'filename': file.filename,
        'parts_read': total,
        **({'pages': len(structure['pages'])} if structure.get('pages') else {}),
        'coverage': [
            item['title'] + (f' (tr.{item["page"]})' if item.get('page') else '') for item in outline.get('headings', [])
        ],
        **({'focus': focus} if focus else {}),
        'summary': '\n\n'.join(parts),
        'note': (
            'This summary already covers every part of the file, in order, with page references [tr.N]. Answer '
            'from it now; do not read the file again to check it. Open a specific page only if the user asks for '
            'the exact wording of one part.'
        ),
    }
