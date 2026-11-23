"""AI answers (Markdown) as Telegram HTML: bold / italic / code / links, headings as bold lines, tables as lines,
reasoning and tool-call blocks removed; long answers cut into messages without breaking a tag."""

import html
import re

LIMIT = 3500  # Markdown characters per message (HTML adds a little)

_DETAILS = re.compile(r'<details\b[^>]*>.*?</details>', re.S | re.I)
_THINK = re.compile(r'<(think|thinking|reasoning)>.*?</\1>', re.S | re.I)
_SOURCE_TAGS = re.compile(r'</?(source|citation)[^>]*>', re.I)
_CITATION = re.compile(r'\s?\[(\d+(?:,\s*\d+)*)\](?!\()')
_FENCE = re.compile(r'```[^\n`]*\n?(.*?)```', re.S)
_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')
_BOLD = re.compile(r'(\*\*|__)(?=\S)(.+?)(?<=\S)\1')
_ITALIC = re.compile(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])')
_STRIKE = re.compile(r'~~(?=\S)(.+?)(?<=\S)~~')
_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def clean(markdown: str) -> str:
    """The answer without reasoning, tool calls, source tags and [1] citations."""
    text = _DETAILS.sub('', markdown or '')
    text = _THINK.sub('', text)
    text = _SOURCE_TAGS.sub('', text)
    text = _CITATION.sub('', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _inline(text: str) -> str:
    """One line of Markdown (no code fences) as Telegram HTML."""
    codes: list[str] = []

    def keep_code(match):
        codes.append(f'<code>{html.escape(match.group(1), quote=False)}</code>')
        return f'\x00{len(codes) - 1}\x00'

    links: list[str] = []

    def keep_link(match):
        label = html.escape(match.group(1), quote=False)
        links.append(f'<a href="{html.escape(match.group(2), quote=True)}">{label}</a>')
        return f'\x01{len(links) - 1}\x01'

    text = _INLINE_CODE.sub(keep_code, text)
    text = _LINK.sub(keep_link, text)
    text = html.escape(text, quote=False)
    text = _BOLD.sub(lambda m: f'<b>{m.group(2)}</b>', text)
    text = _STRIKE.sub(lambda m: f'<s>{m.group(1)}</s>', text)
    text = _ITALIC.sub(lambda m: f'<i>{m.group(1)}</i>', text)
    text = re.sub(r'\x01(\d+)\x01', lambda m: links[int(m.group(1))], text)
    return re.sub(r'\x00(\d+)\x00', lambda m: codes[int(m.group(1))], text)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _block(text: str) -> str:
    """Markdown without code fences as Telegram HTML, line by line."""
    out: list[str] = []
    header: list[str] = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('|') and stripped.count('|') >= 2:
            if _TABLE_SEP.match(stripped):
                continue
            cells = _cells(stripped)
            if not header:
                header = cells
                out.append('<b>' + _inline(re.sub(r'\*\*|__', '', ' · '.join(cells))) + '</b>')
            else:
                pairs = [f'{h}: {c}' if h and c and len(header) == len(cells) and len(cells) <= 4 else c for h, c in zip(header, cells)]
                out.append('• ' + _inline(' · '.join(p for p in pairs if p)))
            continue
        header = []
        heading = _HEADING.match(line)
        if heading:
            title = re.sub(r'\*\*|__', '', heading.group(1))
            out.append(f'<b>{_inline(title)}</b>')
            continue
        if re.match(r'^\s*([-*_])\s*\1\s*\1[\s\1]*$', line):
            out.append('──────────')
            continue
        bullet = re.match(r'^(\s*)[-*+]\s+(.*)$', line)
        if bullet:
            indent = '   ' * (len(bullet.group(1).replace('\t', '    ')) // 2)
            out.append(f'{indent}• {_inline(bullet.group(2))}')
            continue
        quote = re.match(r'^\s*>\s?(.*)$', line)
        if quote:
            out.append(f'▎<i>{_inline(quote.group(1))}</i>')
            continue
        out.append(_inline(line))
    return '\n'.join(out)


def to_html(markdown: str) -> str:
    """Markdown (already cleaned) as Telegram HTML."""
    parts, last = [], 0
    for match in _FENCE.finditer(markdown):
        parts.append(_block(markdown[last : match.start()]))
        parts.append(f'<pre>{html.escape(match.group(1).rstrip(), quote=False)}</pre>')
        last = match.end()
    parts.append(_block(markdown[last:]))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(p for p in parts if p is not None)).strip()


def chunks(markdown: str, limit: int = LIMIT) -> list[str]:
    """Markdown cut at paragraph / line breaks into pieces of about `limit` characters, never inside a code fence."""
    pieces, current, in_fence = [], [], False
    size = 0
    for line in markdown.split('\n'):
        fence = line.strip().startswith('```')
        if size + len(line) > limit and current:
            if not in_fence:
                pieces.append('\n'.join(current))
                current, size = [], 0
            elif not fence:  # a code block longer than a message: close it here, reopen in the next one
                pieces.append('\n'.join([*current, '```']))
                current, size = ['```'], 4
        while len(line) > limit:  # one very long line
            pieces.append(line[:limit])
            line = line[limit:]
        current.append(line)
        size += len(line) + 1
        if fence:
            in_fence = not in_fence
    if current and '\n'.join(current).strip():
        pieces.append('\n'.join(current))
    return pieces or ['']


def messages(markdown: str, header: str = '') -> list[str]:
    """The answer as Telegram HTML messages; `header` (HTML) starts the first one."""
    out = []
    for index, piece in enumerate(chunks(clean(markdown))):
        body = to_html(piece)
        if len(body) > 4000:  # very dense formatting: fall back to escaped text
            body = html.escape(piece, quote=False)[:4000]
        if index == 0 and header:
            body = f'{header}\n{body}' if body else header
        out.append(body)
    return out or [header or '…']


def preview(markdown: str, header: str = '', limit: int = 3500) -> str:
    """The end of an answer still being written, for the progress message (one message)."""
    text = clean(markdown)
    if len(text) > limit:
        text = '…' + text[-limit:]
        nl = text.find('\n')
        if 0 < nl < 200:
            text = '…' + text[nl + 1 :]
    if text.count('```') % 2:
        text += '\n```'
    body = to_html(text)
    if len(body) > 4000:
        body = html.escape(text[-3500:], quote=False)
    return f'{header}\n{body}' if header else body


def short(text: str, length: int = 60) -> str:
    text = re.sub(r'\s+', ' ', text or '').strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + '…'
