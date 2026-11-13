"""Check the document extraction engine with sample files whose text is known.

Runs the same Loader that uploaded files go through, with the saved settings or the values being edited on the
monitor page (so a new engine/URL can be checked before saving). Samples: a PDF with a text layer, a scanned PDF
(the text only as an image) and an image file. For each, how much of the known text came back, with and without
Vietnamese accents, so a reader that drops the accents is visible.
"""

import asyncio
import io
import os
import re
import tempfile
import time
import unicodedata

SAMPLE_TEXT = (
    'NCS AI kiểm tra trích xuất tài liệu. Hợp đồng số HĐ-2026/115 có giá trị 1.250.000.000 đồng. '
    'Người phụ trách: Nguyễn Văn Triển, hạn nghiệm thu ngày 20/11/2026.'
)
TIMEOUT_SECONDS = 120


def _words(text: str, accents: bool = True) -> list[str]:
    text = (text or '').lower()
    if not accents:
        text = unicodedata.normalize('NFD', text).replace('đ', 'd')
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return re.findall(r'[\w./-]+', text)


def _score(found: str, accents: bool = True) -> int:
    expected = _words(SAMPLE_TEXT, accents)
    have = set(_words(found, accents))
    return round(100 * sum(1 for w in expected if w in have) / len(expected)) if expected else 0


def _font() -> str:
    from open_webui.tracker.export import _find_fonts

    fonts = _find_fonts()
    if not fonts:
        raise RuntimeError('Không có font tiếng Việt để tạo file mẫu')
    return fonts[0]


def _image_bytes() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(_font(), 30)
    lines, line = [], ''
    for word in SAMPLE_TEXT.split():
        if len(line) + len(word) > 48:
            lines.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    lines.append(line)
    image = Image.new('RGB', (1000, 60 + 46 * len(lines)), 'white')
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(lines):
        draw.text((30, 30 + 46 * i), text, font=font, fill='black')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def _text_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format='A4')
    pdf.add_font('Body', '', _font())
    pdf.add_page()
    pdf.set_font('Body', '', 13)
    pdf.multi_cell(0, 8, SAMPLE_TEXT)
    return bytes(pdf.output())


def _scanned_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format='A4')
    pdf.add_page()
    pdf.image(io.BytesIO(_image_bytes()), x=10, y=20, w=190)
    return bytes(pdf.output())


async def _config(overrides: dict) -> dict:
    from open_webui.models.config import Config
    from open_webui.routers.retrieval import RETRIEVAL_CONFIG_KEYS

    config = await Config.get_many(*RETRIEVAL_CONFIG_KEYS.values())
    for key, value in (overrides or {}).items():
        if not key.startswith('rag.'):
            continue
        if key == 'rag.content_extraction.supported_media_mime_types' and isinstance(value, str):
            value = [v.strip() for v in value.split(',') if v.strip()] or None
        config[key] = value
    return config


ERROR_TEXT_PREFIXES = ('Error during OCR processing:', 'Error during processing:')
# Engines whose own loader tags the documents with processing_engine; any other value means it was not called.
ENGINE_TAGS = {'paddleocr_vl': 'paddleocr-vl'}


def _api_error(message: str) -> str:
    message = ' '.join(message.split())
    for prefix in ERROR_TEXT_PREFIXES:
        if message.startswith(prefix):
            message = message[len(prefix) :].strip()
    hints = (
        ('404', 'sai đường dẫn API (kiểm tra API Base URL, thường không có /v1)'),
        ('401', 'sai token hoặc sai kiểu xác thực (token/Bearer)'),
        ('403', 'sai token hoặc sai kiểu xác thực (token/Bearer)'),
        ('524', 'proxy RunPod ngắt vì quá 100 giây'),
        ('502', 'máy OCR đang tắt hoặc lỗi'),
        ('503', 'máy OCR đang tắt hoặc quá tải'),
        ('Connection', 'không kết nối được tới máy OCR'),
        ('Max retries', 'không kết nối được tới máy OCR'),
    )
    if message.startswith(('RuntimeError: ', 'ValueError: ')):
        message = message.split(': ', 1)[1]
    # The PaddleOCR-VL loader already explains its HTTP errors.
    hint = '' if 'PaddleOCR-VL' in message else next((h for code, h in hints if code in message), '')
    return (f'{message} ({hint})' if hint else message)[:500]


def _engine_note(config: dict, used: list) -> str:
    engine = config.get('rag.content_extraction_engine') or ''
    tag = ENGINE_TAGS.get(engine)
    if tag and tag not in used:
        return f'Không gọi {engine}: thiếu URL hoặc token nên NCS AI đã dùng bộ đọc mặc định.'
    return ''


async def _run(config: dict, user, name: str, content_type: str, data: bytes) -> dict:
    from open_webui.retrieval.utils import build_loader_from_config

    suffix = os.path.splitext(name)[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = handle.name
    started = time.perf_counter()
    try:
        loader = build_loader_from_config(config)
        loader.user = user
        loader.metadata = {'file_name': name, 'file_content_type': content_type}
        docs = await asyncio.wait_for(loader.aload(name, content_type, path), TIMEOUT_SECONDS)
        elapsed = round((time.perf_counter() - started) * 1000)
        docs = docs or []
        # Some loaders return their error message as the document text instead of raising: that is a failed call.
        for d in docs:
            content = (d.page_content or '').strip()
            if (d.metadata or {}).get('error') == 'processing_failed' or content.startswith(ERROR_TEXT_PREFIXES):
                return {'ok': False, 'stage': 'api', 'ms': elapsed, 'error': _api_error(content)}
        used = sorted({str((d.metadata or {}).get('processing_engine')) for d in docs if (d.metadata or {}).get('processing_engine')})
        empty = all((d.metadata or {}).get('error') == 'no_valid_pages' for d in docs) if docs else True
        text = '' if empty else ' '.join(d.page_content for d in docs)
        return {
            'ok': True,
            'stage': 'content',
            'ms': elapsed,
            'chars': len(text),
            'empty': empty,
            'engines_used': used,
            'engine_note': _engine_note(config, used),
            'score': _score(text),
            'score_no_accents': _score(text, accents=False),
            'preview': text.strip()[:400],
        }
    except asyncio.TimeoutError:
        return {
            'ok': False,
            'stage': 'api',
            'ms': TIMEOUT_SECONDS * 1000,
            'error': f'Quá {TIMEOUT_SECONDS} giây không có kết quả',
        }
    except Exception as e:
        if 'không đọc được chữ nào' in str(e):
            # The engine answered, with no text at all.
            return {
                'ok': True,
                'stage': 'content',
                'ms': round((time.perf_counter() - started) * 1000),
                'chars': 0,
                'empty': True,
                'engines_used': [],
                'engine_note': '',
                'score': 0,
                'score_no_accents': 0,
                'preview': '',
            }
        return {
            'ok': False,
            'stage': 'api',
            'ms': round((time.perf_counter() - started) * 1000),
            'error': _api_error(f'{type(e).__name__}: {e}'),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def run_test(overrides: dict) -> dict:
    from open_webui.models.users import Users
    from open_webui.routers.files import _media_supported_for_extraction

    config = await _config(overrides)
    engine = config.get('rag.content_extraction_engine') or ''
    user = await Users.get_super_admin_user()
    results = []

    sample = await _run(config, user, 'ncs-kiem-tra.pdf', 'application/pdf', _text_pdf())
    results.append({'sample': 'PDF có chữ', 'required': True, **sample})
    scanned = await _run(config, user, 'ncs-kiem-tra-scan.pdf', 'application/pdf', _scanned_pdf())
    results.append({'sample': 'PDF scan (chữ chỉ có trong ảnh)', 'required': False, **scanned})
    if _media_supported_for_extraction(
        engine, config.get('rag.content_extraction.supported_media_mime_types'), 'image/png'
    ):
        image = await _run(config, user, 'ncs-kiem-tra.png', 'image/png', _image_bytes())
        results.append({'sample': 'Ảnh PNG', 'required': False, **image})
    else:
        results.append(
            {
                'sample': 'Ảnh PNG',
                'required': False,
                'skipped': True,
                'note': 'Engine này chưa được bật đọc ảnh (thêm image/* vào "Các loại MIME phương tiện")',
            }
        )

    main = results[0]
    passed = main.get('ok') and main.get('score', 0) >= 80
    return {
        'engine': engine or 'default',
        'passed': bool(passed),
        'expected': SAMPLE_TEXT,
        'results': results,
    }
