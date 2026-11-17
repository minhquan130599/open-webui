"""Spreadsheets read as grids: sheet profiles, header detection, filtered queries.

Used by the query_table tool (questions on any xlsx/csv without the terminal) and by the tracker's task-table
import, which lets a model describe the table and then applies that description to every row here.
"""

import csv
import datetime as dt
import io
import re
from typing import Any, Optional

MAX_ROWS = 20_000
MAX_COLS = 200
CELL_CHARS = 2000


def _cell(value: Any) -> str:
    # An empty cell read through pandas is NaN (a float that is not equal to itself), not the text "nan".
    if value is None or (isinstance(value, float) and value != value):
        return ''
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=' ', timespec='minutes')
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()[:CELL_CHARS]


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Drop trailing empty rows and columns (formatted but unused ranges)."""
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, v in enumerate(row) if v), default=0) for row in rows), default=0)
    return [row[:width] + [''] * (width - len(row[:width])) for row in rows]


def _fill_down(rows: list[list[str]], ranges: list[tuple[int, int, int, int]]) -> None:
    """A cell merged over several rows (a date or a group written once for its rows) holds its value in every row it
    covers. (top, left, bottom, right) are 0-based and inclusive; the value stays in the range's first column, so a
    title merged across columns is not repeated along the row."""
    for top, left, bottom, _right in ranges:
        if bottom <= top or top >= len(rows) or left >= len(rows[top]):
            continue
        value = rows[top][left]
        for r in range(top + 1, min(bottom, len(rows) - 1) + 1):
            if left < len(rows[r]) and not rows[r][left]:
                rows[r][left] = value


def read_grid(path: str, filename: str, fill_merged: bool = False) -> list[dict]:
    """[{'name', 'rows': [[str]], 'merged': n}] for every sheet (csv: one sheet). fill_merged: cells merged over
    several rows repeat their value in each of those rows (Excel files)."""
    lower = (filename or path).lower()
    if lower.endswith(('.csv', '.tsv')):
        raw = open(path, 'rb').read()
        for encoding in ('utf-8-sig', 'cp1258', 'cp1252', 'latin-1'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel_tab if lower.endswith('.tsv') else csv.excel
        rows = [[_cell(v) for v in row[:MAX_COLS]] for _, row in zip(range(MAX_ROWS), csv.reader(io.StringIO(text), dialect))]
        return [{'name': 'CSV', 'rows': _trim(rows), 'merged': 0}]
    if lower.endswith(('.xlsx', '.xlsm')):
        import openpyxl

        workbook = openpyxl.load_workbook(path, data_only=True)
        sheets = []
        for ws in workbook.worksheets:
            rows = [
                [_cell(v) for v in row[:MAX_COLS]]
                for _, row in zip(range(MAX_ROWS), ws.iter_rows(values_only=True))
            ]
            if fill_merged:
                _fill_down(rows, [(m.min_row - 1, m.min_col - 1, m.max_row - 1, m.max_col - 1) for m in ws.merged_cells.ranges])
            sheets.append({'name': ws.title, 'rows': _trim(rows), 'merged': len(ws.merged_cells.ranges)})
        return sheets
    if lower.endswith('.xls') and fill_merged:
        try:
            import xlrd

            book = xlrd.open_workbook(path, formatting_info=True)
            sheets = []
            for ws in book.sheets():
                rows = [
                    [_cell(_xls_value(book, ws.cell(r, c))) for c in range(min(ws.ncols, MAX_COLS))]
                    for r in range(min(ws.nrows, MAX_ROWS))
                ]
                _fill_down(rows, [(r0, c0, r1 - 1, c1 - 1) for r0, r1, c0, c1 in ws.merged_cells])
                sheets.append({'name': ws.name, 'rows': _trim(rows), 'merged': len(ws.merged_cells)})
            return sheets
        except Exception:
            pass  # read below without the merge information
    import pandas as pd  # .xls / .ods when their engines are installed

    frames = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    return [
        {'name': str(name), 'rows': _trim([[_cell(v) for v in row[:MAX_COLS]] for row in frame.values.tolist()[:MAX_ROWS]]), 'merged': 0}
        for name, frame in frames.items()
    ]


def _xls_value(book, cell) -> Any:
    import xlrd

    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
        except Exception:
            return cell.value
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    return cell.value


def column_letter(index: int) -> str:
    letters = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def detect_header(rows: list[list[str]], scan: int = 20) -> int:
    """0-based index of the likely header row: the row among the first ones with the most distinct text cells
    that is followed by data. A guess for tables nobody described; the task import asks a model instead."""
    best, best_score = 0, -1
    for index, row in enumerate(rows[:scan]):
        texts = [v for v in row if v and not _NUMBER_RE.fullmatch(v) and not parse_date(v)]
        if len(texts) < 2 or index + 1 >= len(rows):
            continue
        following = sum(1 for r in rows[index + 1 : index + 6] if sum(1 for v in r if v) >= max(2, len(texts) // 3))
        score = len(set(texts)) + 2 * following
        if score > best_score:
            best, best_score = index, score
    return best


def table_rows(rows: list[list[str]], header_index: int) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(column names, [(1-based sheet row number, cells)]) below the header, skipping empty rows."""
    header = rows[header_index] if header_index < len(rows) else []
    names, seen = [], {}
    for i, value in enumerate(header):
        name = value or f'Cột {column_letter(i)}'
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f'{name} ({seen[name]})')
    # A data row has text in some cell, or its first column filled. Templates pre-fill hundreds of rows with formulas
    # (FALSE, 0): rows of numbers and flags only, with an empty first column, are not rows of the table. A table
    # that starts in column B, or rows with few cells filled, are kept.
    data = [
        (header_index + 2 + offset, row)
        for offset, row in enumerate(rows[header_index + 1 :])
        if (row and row[0]) or any(_has_text(v) for v in row)
    ]
    return names, data


def _has_text(value: str) -> bool:
    value = (value or '').strip()
    return bool(value) and value.upper() not in ('TRUE', 'FALSE') and parse_number(value.rstrip('%')) is None


def sheet_profile(sheet: dict, sample: int = 3) -> dict:
    """Header guess, columns, row count and a few sample rows of one sheet."""
    rows = sheet['rows']
    if not rows:
        return {'name': sheet['name'], 'rows': 0, 'columns': []}
    header = detect_header(rows)
    names, data = table_rows(rows, header)
    return {
        'name': sheet['name'],
        'header_row': header + 1,
        'rows': len(data),
        'columns': names,
        'sample': [dict(zip(names, cells)) for _, cells in data[:sample]],
    }


def profile_text(sheets: list[dict], max_chars: int = 1500) -> str:
    """One line per sheet for the attachment block: "Sheet (N dòng, tiêu đề dòng H): cột A, cột B, ..."."""
    lines = []
    for sheet in sheets:
        profile = sheet_profile(sheet, sample=0)
        if not profile['rows']:
            continue
        columns = ', '.join(c for c in profile['columns'] if not c.startswith('Cột '))[:400]
        lines.append(f'{profile["name"]} ({profile["rows"]} dòng, tiêu đề dòng {profile["header_row"]}): {columns}')
    text = '; '.join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + '…'


_NUMBER_RE = re.compile(r'[-+]?\d{1,3}([.,]\d{3})*([.,]\d+)?%?|[-+]?\d+([.,]\d+)?%?')
_DMY_RE = re.compile(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})')
_YMD_RE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')


def parse_date(value: str, day_first: bool = True) -> Optional[str]:
    """YYYY-MM-DD from "2026-11-30", "30/11/2026" (or 11/30/2026 with day_first=False), else None."""
    text = (value or '').strip()[:19]
    match = _YMD_RE.match(text)
    try:
        if match:
            return dt.date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        match = _DMY_RE.match(text)
        if match:
            a, b, year = int(match[1]), int(match[2]), int(match[3])
            day, month = (a, b) if day_first else (b, a)
            return dt.date(year, month, day).isoformat()
    except ValueError:
        return None
    return None


def parse_number(value: str) -> Optional[float]:
    text = (value or '').strip().replace(' ', '')
    if not text or not _NUMBER_RE.fullmatch(text):
        return None
    text = text.rstrip('%')
    if text.count(',') and text.count('.'):
        text = text.replace('.', '').replace(',', '.') if text.rfind(',') > text.rfind('.') else text.replace(',', '')
    elif text.count(',') == 1 and len(text.split(',')[1]) != 3:
        text = text.replace(',', '.')
    else:
        text = text.replace(',', '').replace('.', '') if re.fullmatch(r'\d{1,3}([.,]\d{3})+', text) else text
    try:
        return float(text)
    except ValueError:
        return None


def _comparable(value: str):
    """A date, a number, or the lower-cased text, so filters compare like with like."""
    return parse_date(value) or parse_number(value) or (value or '').strip().lower()


def _match(cell: str, op: str, value: Any) -> bool:
    op = (op or 'eq').lower()
    if op in ('empty', 'is_empty'):
        return not cell.strip()
    if op in ('not_empty', 'notempty'):
        return bool(cell.strip())
    if op in ('contains', 'not_contains'):
        found = str(value or '').strip().lower() in cell.lower()
        return found if op == 'contains' else not found
    if op == 'in':
        options = value if isinstance(value, list) else str(value or '').split(',')
        return cell.strip().lower() in {str(o).strip().lower() for o in options}
    if op == 'between':
        low, high = (value if isinstance(value, list) and len(value) == 2 else [None, None])
        left = _comparable(cell)
        try:
            return (low is None or left >= _comparable(str(low))) and (high is None or left <= _comparable(str(high)))
        except TypeError:
            return False
    left, right = _comparable(cell), _comparable(str(value if value is not None else ''))
    try:
        return {
            'eq': left == right,
            'ne': left != right,
            'gt': left > right,
            'gte': left >= right,
            'lt': left < right,
            'lte': left <= right,
        }[op]
    except (KeyError, TypeError):
        return False


def resolve_column(names: list[str], wanted: str) -> Optional[int]:
    """Column index by exact name, case-insensitive name, letter (A, B, AA) or unique partial name."""
    wanted = (wanted or '').strip()
    if wanted in names:
        return names.index(wanted)
    lowered = [n.lower() for n in names]
    if wanted.lower() in lowered:
        return lowered.index(wanted.lower())
    if re.fullmatch(r'[A-Za-z]{1,3}', wanted):
        index = 0
        for char in wanted.upper():
            index = index * 26 + ord(char) - 64
        if 0 < index <= len(names):
            return index - 1
    partial = [i for i, n in enumerate(lowered) if wanted.lower() and wanted.lower() in n]
    return partial[0] if len(partial) == 1 else None


def query(
    sheets: list[dict],
    sheet: Optional[str],
    filters: Optional[list[dict]],
    columns: Optional[list[str]],
    sort_by: Optional[str],
    descending: bool,
    limit: int,
    group_by: Optional[str],
    header_row: Optional[int],
    max_chars: int,
) -> dict:
    """Rows of one sheet matching every filter, or counts per group_by value."""
    if not sheets:
        return {'error': 'File has no sheet'}
    target = sheets[0]
    if sheet:
        target = next((s for s in sheets if s['name'].lower() == str(sheet).strip().lower()), None)
        if target is None and str(sheet).strip().isdigit() and 1 <= int(sheet) <= len(sheets):
            target = sheets[int(sheet) - 1]
        if target is None:
            return {'error': f'Sheet "{sheet}" not found', 'sheets': [s['name'] for s in sheets]}
    rows = target['rows']
    header_index = (header_row - 1) if header_row else detect_header(rows)
    names, data = table_rows(rows, header_index)
    result: dict = {'sheet': target['name'], 'header_row': header_index + 1, 'columns': names}

    for condition in filters or []:
        index = resolve_column(names, str(condition.get('column', '')))
        if index is None:
            return {**result, 'error': f'Column "{condition.get("column")}" not found'}
        data = [(n, cells) for n, cells in data if _match(cells[index] if index < len(cells) else '', condition.get('op', 'eq'), condition.get('value'))]
    result['matched_rows'] = len(data)

    if group_by:
        index = resolve_column(names, group_by)
        if index is None:
            return {**result, 'error': f'Column "{group_by}" not found'}
        counts: dict[str, int] = {}
        for _, cells in data:
            key = (cells[index] if index < len(cells) else '') or '(trống)'
            counts[key] = counts.get(key, 0) + 1
        result['groups'] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
        return result

    if sort_by:
        index = resolve_column(names, sort_by)
        if index is None:
            return {**result, 'error': f'Column "{sort_by}" not found'}

        def key(entry):
            value = _comparable(entry[1][index] if index < len(entry[1]) else '')
            return (0, value) if isinstance(value, (int, float)) else (1, str(value))

        data = sorted(data, key=key, reverse=bool(descending))

    picked = list(range(len(names)))
    if columns:
        picked = [i for i in (resolve_column(names, c) for c in columns) if i is not None] or picked
    out, used = [], 0
    for number, cells in data[: max(1, min(int(limit or 50), 500))]:
        row = {'row': number, **{names[i]: cells[i] for i in picked if i < len(cells) and cells[i]}}
        size = len(str(row))
        if out and used + size > max_chars:
            result['truncated'] = True
            break
        out.append(row)
        used += size
    result['rows'] = out
    if len(out) < len(data):
        result['note'] = f'{len(out)} of {len(data)} matching rows shown; narrow the filters, pick columns, or raise limit.'
    return result
