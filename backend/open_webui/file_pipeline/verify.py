"""Links under an answer about files: each page it cites, like [tr.12], links to that page's image in the PDF."""

import re
from typing import Optional

_CITATION_RE = re.compile(r'\[\s*tr\.?\s*(\d{1,4})(?:\s*[-–]\s*\d{1,4})?[^\]]*\]', re.IGNORECASE)
FILE_TOOLS = {'view_file', 'query_chat_files', 'grep_chat_files', 'summarize_chat_file', 'file_outline', 'view_pdf_page'}


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
    if pdf_file:
        pages = sorted(cited_pages(answer))[:limit_links]
        if pages:
            links = ' · '.join(f'[tr.{page}](/api/v1/files/{pdf_file["id"]}/pages/{page}/image)' for page in pages)
            parts.append(f'Xem trang gốc ({pdf_file["name"]}): {links}')
    return '\n\n'.join(parts)
