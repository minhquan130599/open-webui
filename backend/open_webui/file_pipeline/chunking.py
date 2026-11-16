"""Search chunks that follow the document's own structure.

Each chunk is one article / section (long ones are cut on paragraph boundaries) and starts with where it sits in
the document, e.g. "[Nghị định 331.pdf › Chương II › Điều 12 | tr.27-28]". Search then returns whole articles
with their page, so the model can answer and cite without opening the file again.
"""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from open_webui.utils.file_structure import _headings, pages_between


def _page_label(pages: list[int]) -> str:
    if not pages:
        return ''
    return f'tr.{pages[0]}' if len(pages) == 1 else f'tr.{pages[0]}-{pages[-1]}'


def structure_chunks(filename: str, content: str, structure: dict, max_chars: int) -> list[Document]:
    headings = [(offset, level, title) for offset, level, title in _headings(content) if level <= 3]
    sheets = [(start, name) for name, start, _ in structure.get('sheets') or []]
    cuts = sorted({0} | {offset for offset, _, _ in headings} | {start for start, _ in sheets})
    heading_at = {offset: (level, title) for offset, level, title in headings}
    sheet_at = dict(sheets)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chars, chunk_overlap=min(200, max_chars // 10), add_start_index=True
    )

    trail: dict[int, str] = {}
    sheet = None
    chunks = []
    for index, start in enumerate(cuts):
        end = cuts[index + 1] if index + 1 < len(cuts) else len(content)
        if start in sheet_at:
            sheet, trail = sheet_at[start], {}
        if start in heading_at:
            level, title = heading_at[start]
            trail = {lvl: name for lvl, name in trail.items() if lvl < level}
            trail[level] = title
        text = content[start:end].strip()
        if not text:
            continue
        path = ([f'Sheet {sheet}'] if sheet else []) + [trail[lvl] for lvl in sorted(trail)]
        pieces = [(0, text)] if len(text) <= max_chars else [
            (piece.metadata.get('start_index', 0), piece.page_content) for piece in splitter.create_documents([text])
        ]
        for offset, piece in pieces:
            piece_start = start + offset
            pages = pages_between(structure, piece_start, piece_start + len(piece))
            location = ' › '.join([filename, *path])
            label = _page_label(pages)
            header = f'[{location}{" | " + label if label else ""}]'
            metadata = {'start_index': piece_start, 'section': ' › '.join(path) or None}
            if pages:
                metadata.update(page=pages[0] - 1, page_label=pages[0], pages=label)
            chunks.append(Document(page_content=f'{header}\n{piece}', metadata=metadata))
    return chunks
