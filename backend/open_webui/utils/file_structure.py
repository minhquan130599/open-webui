"""Layout of extracted file text: page and sheet offsets, OCR quality, outlines, summary segments, page images.

The extracted text in file.data['content'] stays exactly as before; the layout is stored next to it in
file.data['structure'] as character offsets, so readers can address a page or sheet without re-extracting.
"""

import base64
import bisect
import io
import re
import unicodedata
from typing import Optional

STRUCTURE_VERSION = 1

_WORD_RE = re.compile(r'\w+', re.UNICODE)
_VOWELS = set('aeiouy')
_MARKDOWN_HEADING_RE = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t#]*$', re.MULTILINE)
# Vietnamese legal and report structure: "Chương II", "Mục 3", "Điều 15. ...", "PHẦN I".
_SECTION_RE = re.compile(
    r'^[ \t>*_]*((?:PHẦN|Phần|CHƯƠNG|Chương|MỤC|Mục|ĐIỀU|Điều)[ \t]+[0-9IVXLC]+[a-zđ]?\b[^\n]{0,150})$',
    re.MULTILINE,
)
_SHEET_HEADER_RE = re.compile(r'^Sheet: (.+)$', re.MULTILINE)
_SECTION_LEVELS = {'phần': 1, 'chương': 1, 'mục': 2, 'điều': 3}


def is_pdf(filename: str, content_type: Optional[str]) -> bool:
    return (content_type or '').lower() == 'application/pdf' or (filename or '').lower().endswith('.pdf')


def _doc_page(metadata: dict) -> Optional[int]:
    """1-based page number of a loader document, if the loader recorded one."""
    page = metadata.get('page')
    if isinstance(page, bool):
        return None
    if isinstance(page, int):
        return page + 1
    if isinstance(page, str) and page.isdigit():
        return int(page) + 1
    page_number = metadata.get('page_number')
    if isinstance(page_number, int) and not isinstance(page_number, bool):
        return page_number
    return None


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize('NFD', text)
    return ''.join(c for c in text if not unicodedata.combining(c)).replace('đ', 'd').replace('Đ', 'D')


def text_quality_issues(text: str) -> list[str]:
    """Signs that OCR text of one page is unreliable; empty when the text looks readable."""
    stripped = (text or '').strip()
    if len(stripped) < 40:
        return ['little_text']

    issues = []
    garbled = sum(
        1
        for c in stripped
        if c == '�' or unicodedata.category(c) in ('Co', 'Cn', 'Cs', 'So', 'Sk')
    )
    if garbled / len(stripped) > 0.03:
        issues.append('garbled_chars')

    words = [word for word in _WORD_RE.findall(stripped) if not word.isdigit()]
    # Abbreviations such as HTTT or NĐ are upper case; every other Vietnamese or English word has a vowel.
    letter_words = [word for word in words if word.isalpha() and len(word) >= 2 and not word.isupper()]
    if len(letter_words) >= 20:
        without_vowel = sum(1 for word in letter_words if not set(_strip_accents(word).lower()) & _VOWELS)
        if without_vowel / len(letter_words) > 0.15:
            issues.append('unreadable_words')
    if words and sum(1 for word in words if len(word) > 25) / len(words) > 0.05:
        issues.append('merged_words')
    return issues


# Vietnamese syllable without its tone mark: onset + vowel group + final consonant.
_VI_ONSET = '(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdđghklmnprstvx])?'
_VI_VOWELS = sorted(
    (
        'a ă â e ê i o ô ơ u ư y ai ao au ay âu ây eo êu ia iê iêu iu oa oă oai oao oay oe oeo oi oo ôi ơi ua uâ uây '
        'uê ui uô uôi uơ ươ ươi ươu ưa ưi ưu uy uya uyê uyu yê yêu'
    ).split(),
    key=len,
    reverse=True,
)
_VI_SYLLABLE_RE = re.compile(f'^{_VI_ONSET}(?:{"|".join(_VI_VOWELS)})(?:ng|nh|ch|[cmnpt])?$')
_TONE_MARKS = {'̀', '́', '̃', '̉', '̣'}


def _toneless(word: str) -> str:
    decomposed = unicodedata.normalize('NFD', word.lower())
    return unicodedata.normalize('NFC', ''.join(c for c in decomposed if c not in _TONE_MARKS))


_STOP_FINAL_RE = re.compile(r'(?:ch|[cpt])$')
_NON_STOP_TONES = {'̀', '̃', '̉'}  # huyền, ngã, hỏi


def is_vietnamese_syllable(word: str) -> bool:
    toneless = _toneless(word)
    if not _VI_SYLLABLE_RE.match(toneless):
        return False
    # A syllable ending in p, t, c or ch only takes the sắc or nặng tone ("bắt", "học"; never "bằt").
    if _STOP_FINAL_RE.search(toneless) and _NON_STOP_TONES & set(unicodedata.normalize('NFD', word.lower())):
        return False
    return True


_MARKUP_RE = re.compile(r'<[^>]{0,500}>|&[a-z]{2,8};|&#\d{1,6};', re.IGNORECASE)
# Scripts that never appear in a Vietnamese document unless OCR invented them.
_FOREIGN_SCRIPTS = ('CJK', 'HIRAGANA', 'KATAKANA', 'HANGUL', 'THAI', 'ARABIC', 'HEBREW', 'DEVANAGARI', 'BOPOMOFO')


def _foreign_chars(text: str) -> int:
    count = 0
    for c in text:
        if ord(c) < 0x0370:
            continue  # Latin, including Vietnamese
        try:
            name = unicodedata.name(c)
        except ValueError:
            continue
        if name.startswith(_FOREIGN_SCRIPTS):
            count += 1
    return count


def _page_stats(text: str) -> dict:
    # HTML tables from OCR engines (<td style="text-align...">) are layout, not page text.
    stripped = _MARKUP_RE.sub(' ', text or '').strip()
    stats = {'chars': len(stripped)}
    if not stripped:
        return stats
    stats['foreign'] = _foreign_chars(stripped)
    stats['garbled'] = sum(
        1 for c in stripped if c == '�' or unicodedata.category(c) in ('Co', 'Cn', 'Cs', 'So', 'Sk')
    ) / len(stripped)
    words = [word for word in _WORD_RE.findall(stripped) if word.isalpha()]
    checked = valid = without_vowel = long_words = 0
    for word in words:
        if len(word) > 25:
            long_words += 1
        if word.isupper() and len(word) >= 2 and not is_vietnamese_syllable(word):
            continue  # abbreviation (HTTT, NĐ, CP) or roman numeral
        checked += 1
        valid += is_vietnamese_syllable(word)
        without_vowel += not set(_strip_accents(word).lower()) & _VOWELS
    stats.update(words=checked, vi_valid=valid, without_vowel=without_vowel, long_words=long_words, all_words=len(words))
    return stats


def score_pages(texts: list[str]) -> list[dict]:
    """Quality of each page's extracted text: {'score': 0-100, 'issues': [...], 'vi_ratio': ...}.

    A Vietnamese document is scored by the share of words that are valid Vietnamese syllables, which also catches
    wrong diacritics and mixed-in foreign glyphs; other documents by the share of words that have a vowel. Junk
    characters and run-together words lower the score.
    """
    stats = [_page_stats(text) for text in texts]
    ratios = sorted(s['vi_valid'] / s['words'] for s in stats if s.get('words', 0) >= 15)
    vietnamese = bool(ratios) and ratios[len(ratios) // 2] >= 0.6
    results = []
    for s in stats:
        issues = []
        if s['chars'] < 40:
            results.append({'score': 0, 'issues': ['little_text'], 'chars': s['chars']})
            continue
        words = s.get('words', 0)
        if words >= 15:
            ratio = (s['vi_valid'] if vietnamese else words - s['without_vowel']) / words
        else:
            ratio = 1.0  # too few words to judge (tables of numbers, forms)
        garbled = s.get('garbled', 0)
        merged = s['long_words'] / s['all_words'] if s.get('all_words') else 0
        score = ratio * max(0.0, 1 - 5 * garbled) * max(0.0, 1 - 4 * merged)
        if garbled > 0.03:
            issues.append('garbled_chars')
        if words >= 15 and ratio < 0.8:
            issues.append('unreadable_words')
        if vietnamese and s.get('foreign'):
            # Chinese/Japanese/Korean... glyphs inside Vietnamese text are OCR inventions: never a good page.
            issues.append('foreign_script')
            score = min(score, 0.6)
        if merged > 0.05:
            issues.append('merged_words')
        results.append(
            {
                'score': round(score * 100),
                'issues': issues,
                'chars': s['chars'],
                'words': words,
                'valid_ratio': round(ratio, 3),
                'garbled_ratio': round(garbled, 4),
                'foreign_chars': s.get('foreign', 0),
            }
        )
    return results


def low_pages(scores: dict, min_score: int) -> list[int]:
    return sorted(page for page, score in scores.items() if score < min_score)


def build_file_structure(
    docs: list,
    text_content: str,
    filename: str,
    content_type: Optional[str],
    min_score: int = 80,
    engine: Optional[str] = None,
) -> Optional[dict]:
    """Page/sheet offsets into text_content, which is the loader documents joined with one separator character (a newline).

    For PDFs it also records each page's score and how it was read (text layer or OCR engine).
    """
    pages: dict[int, list[int]] = {}
    methods: dict[int, str] = {}
    sheets: list[list] = []
    position = 0
    for doc in docs:
        text = getattr(doc, 'page_content', '') or ''
        start, end = position, position + len(text)
        position = end + 1
        metadata = getattr(doc, 'metadata', None) or {}
        page = _doc_page(metadata)
        if page is not None:
            if page in pages:
                pages[page][1] = end
            else:
                pages[page] = [start, end]
            if metadata.get('extraction'):
                methods[page] = metadata['extraction']
        sheet = metadata.get('page_name') or metadata.get('sheet_name')
        if sheet:
            if sheets and sheets[-1][0] == str(sheet):
                sheets[-1][2] = end
            else:
                sheets.append([str(sheet), start, end])

    if not sheets:
        # The pandas Excel fallback writes every sheet into one document under a "Sheet: name" line.
        headers = list(_SHEET_HEADER_RE.finditer(text_content))
        if headers and filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
            for index, match in enumerate(headers):
                end = headers[index + 1].start() if index + 1 < len(headers) else len(text_content)
                sheets.append([match.group(1).strip(), match.start(), end])

    structure: dict = {'version': STRUCTURE_VERSION}
    # Spreadsheet loaders number sheets as pages; for them the sheet list is the layout.
    if sheets and not is_pdf(filename, content_type):
        pages = {}
    if len(pages) > 1 or (pages and is_pdf(filename, content_type)):
        structure['pages'] = [[page, start, end] for page, (start, end) in sorted(pages.items())]
        if is_pdf(filename, content_type):
            scored = score_pages([text_content[start:end] for _, start, end in structure['pages']])
            structure['page_scores'] = {
                str(page): result['score'] for (page, _, _), result in zip(structure['pages'], scored)
            }
            structure['low_quality_pages'] = low_pages(
                {int(page): score for page, score in structure['page_scores'].items()}, min_score
            )
            default_method = 'ocr' if engine else 'text_layer'
            structure['page_methods'] = {str(page): methods.get(page, default_method) for page, _, _ in structure['pages']}
    if sheets:
        structure['sheets'] = sheets
    return structure if len(structure) > 1 else None


def get_structure(file) -> dict:
    data = getattr(file, 'data', None) or {}
    structure = data.get('structure')
    return structure if isinstance(structure, dict) else {}


def page_range(structure: dict, page: int) -> Optional[tuple[int, int]]:
    for number, start, end in structure.get('pages') or []:
        if number == page:
            return start, end
    return None


def sheet_range(structure: dict, sheet: str) -> Optional[tuple[str, int, int]]:
    sheets = structure.get('sheets') or []
    wanted = str(sheet).strip().lower()
    for name, start, end in sheets:
        if name.strip().lower() == wanted:
            return name, start, end
    if wanted.isdigit() and 1 <= int(wanted) <= len(sheets):
        name, start, end = sheets[int(wanted) - 1]
        return name, start, end
    return None


def page_at(structure: dict, offset: int) -> Optional[int]:
    pages = structure.get('pages') or []
    starts = [start for _, start, _ in pages]
    index = bisect.bisect_right(starts, offset) - 1
    return pages[index][0] if index >= 0 else None


def pages_between(structure: dict, start: int, end: int) -> list[int]:
    return [page for page, page_start, page_end in structure.get('pages') or [] if page_start < end and page_end > start]


def _headings(content: str) -> list[tuple[int, int, str]]:
    found = {}
    for match in _MARKDOWN_HEADING_RE.finditer(content):
        found[match.start()] = (len(match.group(1)), match.group(2).strip())
    for match in _SECTION_RE.finditer(content):
        if match.start() in found:
            continue
        title = match.group(1).strip().strip('*_ ')
        level = _SECTION_LEVELS.get(title.split()[0].lower(), 3)
        found[match.start()] = (level, title)
    return [(offset, level, title[:160]) for offset, (level, title) in sorted(found.items())]


def build_outline(content: str, structure: dict, max_items: int = 150) -> dict:
    """Headings, pages and sheets of a file, small enough to read before deciding what to open."""
    headings = _headings(content)
    outline: dict = {'total_chars': len(content)}
    if headings:
        top = min(level for _, level, _ in headings)
        # Long documents keep only their upper levels, so the outline still spans the whole file.
        selected = [item for item in headings if item[1] == top][:max_items]
        for depth in range(top, 7):
            kept = [item for item in headings if item[1] <= depth]
            if len(kept) > max_items:
                break
            selected = kept
        outline['headings'] = [
            {
                'title': title,
                'level': level,
                'offset': offset,
                **({'page': page_at(structure, offset)} if structure.get('pages') else {}),
            }
            for offset, level, title in selected
        ]
        if len(selected) < len(headings):
            outline['headings_omitted'] = len(headings) - len(selected)
    if structure.get('pages'):
        outline['pages'] = len(structure['pages'])
    if structure.get('low_quality_pages'):
        outline['low_quality_pages'] = structure['low_quality_pages']
    if structure.get('sheets'):
        outline['sheets'] = [
            {'name': name, 'offset': start, 'chars': end - start, 'lines': content[start:end].count('\n') + 1}
            for name, start, end in structure['sheets']
        ]
    return outline


def split_segments(content: str, structure: dict, max_chars: int) -> list[tuple[int, int]]:
    """Cut content into pieces of at most max_chars, preferring section, sheet and page starts."""
    length = len(content)
    boundaries = sorted(
        {offset for offset, level, _ in _headings(content) if level <= 2}
        | {start for _, start, _ in structure.get('pages') or []}
        | {start for _, start, _ in structure.get('sheets') or []}
    )
    segments = []
    start = 0
    while start < length:
        limit = start + max_chars
        if limit >= length:
            end = length
        else:
            index = bisect.bisect_right(boundaries, limit) - 1
            if index >= 0 and boundaries[index] - start >= max_chars // 3:
                end = boundaries[index]
            else:
                newline = content.rfind('\n', start + max_chars // 2, limit)
                end = newline + 1 if newline != -1 else limit
        segments.append((start, end))
        start = end
    return segments


def pdf_text_layer(path: str) -> list[str]:
    """Text of each page's own text layer (empty for scanned pages), without OCR."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    try:
        texts = []
        for index in range(len(document)):
            page = document[index]
            try:
                textpage = page.get_textpage()
                try:
                    texts.append(textpage.get_text_range().replace('\r\n', '\n').replace('\r', '\n').strip())
                finally:
                    textpage.close()
            finally:
                page.close()
        return texts
    finally:
        document.close()


def pdf_subset(path: str, pages: list[int], out_path: str) -> None:
    """Write the given 0-based pages of a PDF, in order, to out_path."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    for index in pages:
        writer.add_page(reader.pages[index])
    with open(out_path, 'wb') as handle:
        writer.write(handle)


def render_pdf_page(path: str, page: int, max_side: int) -> tuple[str, int]:
    """JPEG data URL of one 1-based PDF page, and the document's page count."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    try:
        page_count = len(document)
        if page < 1 or page > page_count:
            raise ValueError(f'Page {page} is out of range (1-{page_count})')
        pdf_page = document[page - 1]
        try:
            width, height = pdf_page.get_size()
            scale = min(max_side / max(width, height, 1), 4.0)
            image = pdf_page.render(scale=scale).to_pil().convert('RGB')
        finally:
            pdf_page.close()
    finally:
        document.close()
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=85, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'), page_count
