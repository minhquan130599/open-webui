"""Check an answer about files against the file text it was given.

Numbers and dates in the answer must appear in the text the model read (attached file text, tool results);
the ones that do not are listed under the answer so the reader checks them in the original. Page citations
like [tr.12] get links to the page image of the PDF.
"""

import re
from datetime import datetime, timezone
from typing import Optional

_CITATION_RE = re.compile(r'\[\s*tr\.?\s*(\d{1,4})(?:\s*[-–]\s*\d{1,4})?[^\]]*\]', re.IGNORECASE)
_DATE_RE = re.compile(r'\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})\b')
_NUMBER_RE = re.compile(r'(?<![\w.,])\d{1,3}(?:[.,]\d{3})+(?:,\d+)?(?![\w])|(?<![\w.,/])\d+(?:,\d+)?(?:\s?%)?(?![\w/])')
_LIST_MARKER_RE = re.compile(r'^\s*\d{1,3}[.)]\s', re.MULTILINE)
FILE_TOOLS = {'view_file', 'query_chat_files', 'grep_chat_files', 'summarize_chat_file', 'file_outline', 'view_pdf_page'}


def _canonical_date(day: str, month: str, year: str) -> str:
    return f'{int(day)}/{int(month)}/{year}'


def _canonical_number(raw: str) -> str:
    return re.sub(r'[\s.,%]', '', raw)


def _facts(text: str, with_citations: bool = False) -> tuple[set, set]:
    """(dates, numbers) in canonical form."""
    if not with_citations:
        text = _CITATION_RE.sub(' ', text)
        text = _LIST_MARKER_RE.sub(' ', text)
    dates = {_canonical_date(*match.groups()) for match in _DATE_RE.finditer(text)}
    text_without_dates = _DATE_RE.sub(' ', text)
    numbers = {
        _canonical_number(match.group(0))
        for match in _NUMBER_RE.finditer(text_without_dates)
        if len(_canonical_number(match.group(0))) >= 2
    }
    return dates, numbers


def unmatched_facts(answer: str, sources: list[str], limit: int = 12) -> list[str]:
    """Dates and numbers (2+ digits) of the answer that appear in none of the sources."""
    source_text = '\n'.join(sources)
    source_dates, source_numbers = _facts(source_text, with_citations=True)
    # Dates may be written as words in the source ("ngày 19 tháng 8 năm 2026") or as ISO dates (the current
    # date in the system prompt, "2026-11-16"); today's date is always known to the model.
    for day, month, year in re.findall(r'ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})', source_text, re.IGNORECASE):
        source_dates.add(_canonical_date(day, month, year))
    for year, month, day in re.findall(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', source_text):
        source_dates.add(_canonical_date(day, month, year))
    for today in (datetime.now(), datetime.now(timezone.utc)):
        source_dates.add(_canonical_date(str(today.day), str(today.month), str(today.year)))
    source_digits = set(re.findall(r'\d+', source_text))
    answer_dates, answer_numbers = _facts(answer)
    missing = [date for date in sorted(answer_dates) if date not in source_dates]
    missing += [
        number for number in sorted(answer_numbers) if number not in source_numbers and number not in source_digits
    ]
    return missing[:limit]


def cited_pages(answer: str) -> list[int]:
    seen = []
    for match in _CITATION_RE.finditer(answer):
        page = int(match.group(1))
        if page not in seen:
            seen.append(page)
    return seen


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(
            part.get('text') or '' if isinstance(part, dict) else str(part) for part in content
        )
    return ''


def answer_sources(messages: list[dict], output: list[dict]) -> tuple[list[str], bool]:
    """Texts the model was given (not its own earlier answers), and whether a file tool ran this turn."""
    sources = [_text_of(message.get('content')) for message in messages if message.get('role') != 'assistant']
    used_file_tool = False
    names = {item.get('call_id'): item.get('name') for item in output if item.get('type') == 'function_call'}
    for item in output:
        if item.get('type') == 'function_call_output':
            sources.append(_text_of(item.get('output')))
            used_file_tool = used_file_tool or names.get(item.get('call_id')) in FILE_TOOLS
    return sources, used_file_tool


def single_pdf(files: list) -> Optional[dict]:
    pdfs = [
        {'id': item.get('id'), 'name': item.get('name') or item.get('filename') or 'file.pdf'}
        for item in files or []
        if isinstance(item, dict)
        and item.get('id')
        and str(item.get('name') or item.get('filename') or '').lower().endswith('.pdf')
    ]
    return pdfs[0] if len(pdfs) == 1 else None


def verification_note(answer: str, sources: list[str], pdf_file: Optional[dict], limit_links: int = 12) -> str:
    """Markdown appended under the answer, or '' when there is nothing to report."""
    parts = []
    missing = unmatched_facts(answer, sources)
    if missing:
        parts.append(
            '> ⚠️ Chưa đối chiếu được với phần tài liệu đã đọc: '
            + ', '.join(f'`{item}`' for item in missing)
            + '. Hãy kiểm tra lại trong file gốc.'
        )
    if pdf_file:
        pages = sorted(cited_pages(answer))[:limit_links]
        if pages:
            links = ' · '.join(f'[tr.{page}](/api/v1/files/{pdf_file["id"]}/pages/{page}/image)' for page in pages)
            parts.append(f'Xem trang gốc ({pdf_file["name"]}): {links}')
    return '\n\n'.join(parts)
