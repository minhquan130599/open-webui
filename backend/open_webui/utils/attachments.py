"""How attached files are presented to a model: one block with ids, sizes and layout, plus small files inline.

Chat requests and channel mentions both use this, so a model sees attachments the same way everywhere and
never has to guess where a file lives or which tool reads it.
"""

import difflib
import html
import logging
import re
from typing import Optional

from open_webui.utils.file_structure import get_structure, is_pdf

log = logging.getLogger(__name__)

# Names models tend to invent, mapped to the tool that does the job.
TOOL_ALIASES = {
    'skill_read_file': 'read_skill_file',
    'read_skill': 'view_skill',
    'get_skill': 'view_skill',
    'list_skills': 'list_available_skills',
    'read_chat_file': 'view_file',
    'read_attachment': 'view_file',
    'view_attachment': 'view_file',
    'list_attachments': 'list_chat_files',
    'search_chat_files': 'query_chat_files',
}


def unknown_tool_error(name: str, tools: Optional[dict]) -> str:
    """Error for a call to a tool that was not offered, naming the closest real tools."""
    names = list(tools or {})
    alias = TOOL_ALIASES.get(name)
    suggestions = [alias] if alias in names else difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    message = f'Error: Tool "{name}" not found.'
    if suggestions:
        message += f' Did you mean: {", ".join(suggestions)}?'
    return message + ' Only call tools from the provided tool list.'


async def describe_attachments(items: list[dict], user: dict) -> list[dict]:
    """Readable attached files with their size and layout, in the order given."""
    from open_webui.file_pipeline.status import processing_state
    from open_webui.utils.file_structure import is_spreadsheet
    from open_webui.tools.builtin import _get_accessible_chat_files

    described = []
    for item, file in await _get_accessible_chat_files(items, user):
        meta = file.meta or {}
        content = (file.data or {}).get('content') or ''
        structure = get_structure(file)
        info = {
            'id': file.id,
            'name': item.get('name') or file.filename,
            'content_type': item.get('content_type') or meta.get('content_type') or '',
            'size': item.get('size') or meta.get('size'),
            'chars': len(content),
            'content': content,
            'status': (file.data or {}).get('status'),
        }
        if structure.get('pages'):
            info['pages'] = len(structure['pages'])
        elif is_pdf(file.filename, info['content_type']):
            info['pdf'] = True
        if structure.get('low_quality_pages'):
            info['low_quality_pages'] = structure['low_quality_pages']
        if structure.get('sheets'):
            info['sheets'] = [name for name, _, _ in structure['sheets']]
        if (file.data or {}).get('table_profile'):
            info['table_profile'] = file.data['table_profile']
        info['spreadsheet'] = is_spreadsheet(file.filename, info['content_type'])
        state = await processing_state(file.id)
        if state:
            info['processing'] = state['label']
        described.append(info)
    return described


def choose_inline(infos: list[dict], eligible_ids: Optional[set] = None, budget: int = 0) -> set:
    """Ids of files to include in full: smallest first, while they fit the budget together."""
    if budget <= 0:
        return set()
    chosen, used = set(), 0
    candidates = [
        info
        for info in infos
        if info['chars'] > 0 and (eligible_ids is None or info['id'] in eligible_ids)
    ]
    for info in sorted(candidates, key=lambda info: info['chars']):
        if used + info['chars'] > budget:
            break
        chosen.add(info['id'])
        used += info['chars']
    return chosen


def _size_label(size) -> str:
    if not isinstance(size, (int, float)) or size <= 0:
        return ''
    if size >= 1024 * 1024:
        return f'{size / 1024 / 1024:.1f}MB'
    return f'{max(size / 1024, 0.1):.1f}KB'


def format_attachment_block(
    infos: list[dict],
    inline_ids: set,
    *,
    terminal: bool = False,
    vision: bool = False,
    file_tools: bool = True,
    project: bool = False,
) -> str:
    """The <attached_files> block, the included file texts, and how to read the rest."""
    if not infos:
        return ''

    tags = []
    for info in infos:
        attrs = {'id': info['id'], 'name': info['name']}
        if info.get('content_type'):
            attrs['type'] = info['content_type']
        if _size_label(info.get('size')):
            attrs['size'] = _size_label(info['size'])
        attrs['chars'] = str(info['chars'])
        if info.get('pages'):
            attrs['pages'] = str(info['pages'])
        if info.get('table_profile'):
            attrs['tables'] = info['table_profile']
        elif info.get('sheets'):
            attrs['sheets'] = ', '.join(info['sheets'])
        if info.get('low_quality_pages'):
            attrs['low_quality_pages'] = ','.join(str(page) for page in info['low_quality_pages'])
        if info.get('processing'):
            attrs['processing'] = info['processing']
        if info['id'] in inline_ids:
            attrs['content'] = 'included below'
        elif info.get('status') == 'pending':
            attrs['status'] = 'still processing'
        tags.append('<file ' + ' '.join(f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attrs.items()) + '/>')

    parts = ['<attached_files>', *tags, '</attached_files>']
    for info in infos:
        if info['id'] in inline_ids:
            parts.append(
                f'<file_content id="{html.escape(info["id"], quote=True)}" name="{html.escape(info["name"], quote=True)}">\n'
                f'{info["content"]}\n</file_content>'
            )

    guide = [
        'The files above were attached by the user. They are stored in the chat, NOT in the terminal '
        'filesystem: terminal tools such as read_file, list_files, match_files or search_files cannot see them.'
    ]
    if inline_ids:
        guide.append(
            'The full extracted text of the files marked content="included below" is in <file_content>; '
            'read it there, no tool call is needed.'
        )
    if file_tools and len(inline_ids) < len(infos):
        guide.append(
            'For the other files use their id: file_outline for the structure, view_file (page, sheet or offset) '
            'to read a part, query_chat_files to find specific passages, summarize_chat_file for a complete '
            'summary of a long file.'
        )
    if any(info.get('processing') and not info['chars'] for info in infos):
        guide.append(
            'Files with chars="0" have no text yet because they are still being extracted: tell the user to ask '
            'again in a moment; do not look for them in the terminal or anywhere else.'
        )
    if any(info.get('processing') and info['chars'] for info in infos):
        guide.append(
            'Files marked processing are still having their OCR repaired: their text may still contain OCR errors, '
            'so read the pages listed in low_quality_pages from the page image (view_pdf_page) when they matter, '
            'and tell the user the file is still being processed.'
        )
    if vision and any(info.get('low_quality_pages') for info in infos):
        guide.append(
            'Pages listed in low_quality_pages have unreliable OCR text; call view_pdf_page to see the page image.'
        )
    guide.append(
        'When you answer from these files, cite the page after each fact, like [tr.12] (add the article, e.g. '
        '[tr.12, Điều 5], when there is one), quote numbers and dates exactly as written, and say plainly when '
        'the files do not contain the answer instead of guessing.'
    )
    if project:
        guide.append(
            'This channel is a project: questions about its work items, owners, due dates or progress (for example '
            '"việc hạn tháng 12") are answered from the project data with get_project_status / build_project_report '
            '(due_from / due_to), which also includes task tables posted here; it says which files are not in it yet.'
        )
    if any(info.get('spreadsheet') for info in infos):
        guide.append(
            'Spreadsheets (their sheets and columns are listed in tables="..."): filter, count or look up rows with '
            'query_table, in one call; do not use the terminal for that.'
        )
    if terminal:
        guide.append(
            'Use the terminal only to create a file or fill a Word/Excel template: call open_in_terminal with the '
            'file id first and use the returned terminal_path.'
        )
    return '\n'.join(parts) + '\n' + ' '.join(guide)


def log_attachments(where: str, infos: list[dict], inline_ids: set, extra: str = '') -> None:
    if not infos:
        return
    log.info(
        'attachments[%s]: %s%s',
        where,
        '; '.join(
            f'{info["name"]} ({info["id"]}, {info["chars"]} chars{", inline" if info["id"] in inline_ids else ""})'
            for info in infos
        ),
        f' | {extra}' if extra else '',
    )

