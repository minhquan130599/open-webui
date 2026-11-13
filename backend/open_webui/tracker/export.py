"""Report files built in Open WebUI itself (no terminal needed): DOCX, XLSX, PDF and Markdown."""

import io
import os
import re
from typing import Optional

from open_webui.tracker.report import Report, export_markdown, person_stats
from open_webui.tracker.schema import STATUS_LABELS

# Fonts with Vietnamese glyphs: DejaVu from the image (fonts-dejavu-core), then common desktop fallbacks.
_FONT_DIRS = [
    os.environ.get('TRACKER_PDF_FONT_DIR', ''),
    '/usr/share/fonts/truetype/dejavu',
    '/usr/share/fonts/dejavu',
    'C:/Windows/Fonts',
]
_FONT_FILES = [('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf'), ('arial.ttf', 'arialbd.ttf')]

CONTENT_TYPES = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pdf': 'application/pdf',
    'md': 'text/markdown',
}


def _find_fonts() -> Optional[tuple[str, str]]:
    for directory in filter(None, _FONT_DIRS):
        for regular, bold in _FONT_FILES:
            regular_path, bold_path = os.path.join(directory, regular), os.path.join(directory, bold)
            if os.path.isfile(regular_path):
                return regular_path, bold_path if os.path.isfile(bold_path) else regular_path
    return None


def _inline_runs(text: str) -> list[tuple[str, bool]]:
    """Split **bold** markers into (text, bold) runs."""
    parts = re.split(r'(\*\*[^*]+\*\*)', text)
    return [(p[2:-2], True) if p.startswith('**') and p.endswith('**') else (p, False) for p in parts if p]


def _table_rows(block: list[str]) -> list[list[str]]:
    rows = []
    for line in block:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
            continue  # header separator
        rows.append(cells)
    return rows


def to_docx(markdown: str, title: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    document.styles['Normal'].font.size = Pt(10.5)
    document.add_heading(title, level=0)
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if stripped.startswith('|'):
            block = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                block.append(lines[index])
                index += 1
            rows = _table_rows(block)
            if rows:
                table = document.add_table(rows=0, cols=max(len(r) for r in rows))
                table.style = 'Table Grid'
                for row_index, row in enumerate(rows):
                    cells = table.add_row().cells
                    for col, value in enumerate(row):
                        run = cells[col].paragraphs[0].add_run(value)
                        run.bold = row_index == 0
            continue
        index += 1
        if not stripped or re.fullmatch(r'-{3,}', stripped):
            continue
        heading = re.match(r'^(#{1,4})\s+(.*)$', stripped)
        if heading:
            document.add_heading(heading.group(2).replace('**', ''), level=min(len(heading.group(1)), 4))
            continue
        bullet = re.match(r'^[-*+]\s+(.*)$', stripped)
        numbered = re.match(r'^\d+[.)]\s+(.*)$', stripped)
        if bullet:
            paragraph = document.add_paragraph(style='List Bullet')
            text = bullet.group(1)
        elif numbered:
            paragraph = document.add_paragraph(style='List Number')
            text = numbered.group(1)
        else:
            paragraph = document.add_paragraph()
            text = stripped
        for value, bold in _inline_runs(text):
            paragraph.add_run(value).bold = bold
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# Column widths (%) for the appendix tables, by column count; other tables keep equal columns.
_COLUMN_WIDTHS = {
    7: (5, 31, 13, 11, 8, 14, 18),  # #, item, owner, status, progress, due, blocker
    10: (19, 8, 8, 9, 9, 9, 9, 9, 9, 11),  # per-person statistics
}


def _size_columns(html: str) -> str:
    def size(match: re.Match) -> str:
        head = match.group(0)
        cells = re.findall(r'<th(?:\s[^>]*)?>', head)
        widths = _COLUMN_WIDTHS.get(len(cells))
        if not widths:
            return head
        widths = iter(widths)
        return re.sub(r'<th(\s[^>]*)?>', lambda m: f'<th{m.group(1) or ""} width="{next(widths)}%">', head)

    return re.sub(r'<thead>.*?</thead>', size, html, flags=re.S)


def _separate_lists(markdown: str) -> str:
    """Markdown needs a blank line before a list or table that follows a paragraph; models often omit it."""
    out: list[str] = []
    for line in markdown.splitlines():
        starts_block = bool(re.match(r'^\s*([-*+]\s|\d+[.)]\s|\|)', line))
        previous = out[-1] if out else ''
        if starts_block and previous.strip() and not re.match(r'^\s*([-*+]\s|\d+[.)]\s|\|)', previous):
            out.append('')
        out.append(line)
    return '\n'.join(out)


def to_pdf(markdown: str, title: str) -> bytes:
    import markdown as markdown_lib
    from fpdf import FPDF

    fonts = _find_fonts()
    if not fonts:
        raise RuntimeError('No font with Vietnamese characters found for PDF (install fonts-dejavu-core)')
    pdf = FPDF(format='A4')
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 15)
    pdf.add_font('Body', '', fonts[0])
    pdf.add_font('Body', 'B', fonts[1])
    pdf.add_font('Body', 'I', fonts[0])
    pdf.add_font('Body', 'BI', fonts[1])
    pdf.add_page()
    pdf.set_font('Body', 'B', 16)
    pdf.multi_cell(0, 9, title)
    pdf.ln(2)
    pdf.set_font('Body', '', 10)
    html = markdown_lib.markdown(_separate_lists(markdown), extensions=['tables', 'sane_lists'])
    html = re.sub(r'<td>', '<td align="left">', html)
    html = _size_columns(html.replace('<table>', '<table border="1" width="100%">'))
    from fpdf.fonts import TextStyle

    dark = '#1f2937'
    pdf.write_html(
        html,
        font_family='Body',
        table_line_separators=True,
        tag_styles={
            'h1': TextStyle(color=dark, font_size_pt=17, t_margin=5, b_margin=1),
            'h2': TextStyle(color=dark, font_size_pt=14, t_margin=5, b_margin=1),
            'h3': TextStyle(color=dark, font_size_pt=12, t_margin=4, b_margin=1),
            'h4': TextStyle(color=dark, font_size_pt=11, t_margin=3, b_margin=1),
        },
    )
    return bytes(pdf.output())


def to_xlsx(report: Report, narrative: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Hạng mục'
    headers = ['Dự án', 'Hạng mục', 'Phụ trách', 'Trạng thái', 'Tiến độ (%)', 'Hạn', 'Quá hạn', 'Vướng mắc']
    headers += ['Ngày tạo', 'Cập nhật', 'Hoàn thành']
    sheet.append(headers)
    for item in report.items:
        status = STATUS_LABELS.get(item.trang_thai, item.trang_thai)
        if item.archived:
            status = 'Đã hủy' if item.archived == 'huy' else 'Hoàn thành (đã đóng)'
        sheet.append(
            [
                item.project,
                item.ten,
                ', '.join(item.phu_trach),
                status,
                item.tien_do,
                item.han,
                'Có' if item.overdue(report.today) else '',
                item.vuong_mac,
                item.tao_luc,
                item.cap_nhat_luc,
                item.hoan_thanh_luc,
            ]
        )
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip('ABCDEFGHIJK', (18, 45, 22, 14, 11, 12, 9, 35, 12, 12, 12)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = 'A2'

    people = workbook.create_sheet('Theo người')
    people.append(
        ['Người', 'Tổng', 'Xong', 'Đúng hạn', 'Trễ hạn', 'Đang mở', 'Quá hạn', 'Bị chặn', 'Đã hủy', 'Tiến độ TB']
    )
    for owner, s in sorted(person_stats(report.items, report.today).items()):
        people.append(
            [
                owner,
                s['tong'],
                s['xong'],
                s['xong_dung_han'],
                s['xong_tre_han'],
                s['dang_mo'],
                s['qua_han'],
                s['bi_chan'],
                s['da_huy'],
                s['tien_do_tb_viec_dang_mo'],
            ]
        )
    for cell in people[1]:
        cell.font = Font(bold=True)
    people.column_dimensions['A'].width = 24

    notes = workbook.create_sheet('Nhận xét')
    notes.column_dimensions['A'].width = 120
    for line in narrative.strip().splitlines():
        notes.append([line.replace('**', '')])
        notes.cell(row=notes.max_row, column=1).alignment = Alignment(wrap_text=True)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def build_file(report: Report, narrative: str, file_format: str) -> tuple[str, bytes, str]:
    """(file name, bytes, content type) for the report in the requested format."""
    file_format = (file_format or 'pdf').lower().lstrip('.')
    if file_format == 'doc':
        file_format = 'docx'
    if file_format in ('xls', 'excel'):
        file_format = 'xlsx'
    if file_format not in CONTENT_TYPES:
        raise ValueError(f'Unsupported format: {file_format} (use pdf, docx, xlsx or md)')
    title = f'Báo cáo: {report.title}'
    slug = re.sub(r'[^a-z0-9]+', '-', _ascii(report.title).lower()).strip('-')[:40] or 'du-an'
    name = f'bao-cao-{slug}-{report.today}.{file_format}'
    markdown = export_markdown(report, narrative)
    if file_format == 'docx':
        data = to_docx(markdown, title)
    elif file_format == 'pdf':
        data = to_pdf(markdown, title)
    elif file_format == 'xlsx':
        data = to_xlsx(report, narrative)
    else:
        data = f'# {title}\n\n{markdown}'.encode('utf-8')
    return name, data, CONTENT_TYPES[file_format]


def _ascii(text: str) -> str:
    from open_webui.tracker.schema import normalize_key

    return normalize_key(text)
