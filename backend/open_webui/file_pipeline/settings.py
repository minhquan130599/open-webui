"""Editable settings of file processing, shared by the admin Settings > Documents page and the monitor.

Values live in the config table under these keys; a key without a row uses the default below. This is the only
place these limits are set (no environment variables), so what the settings page shows is what runs. Every
reader calls file_settings(), so a change applies within a few seconds without a restart.
"""

import time

FILE_SETTINGS_GROUP = ('files', 'Xử lý file (OCR, sửa lỗi, tóm tắt)', 'Áp dụng trong vài giây, không cần khởi động lại.')

# key, label, type (bool | int | text), default, help, min/max for int.
FILE_SETTINGS: list[dict] = [
    {'key': 'files.pipeline.enable', 'label': 'Xử lý nâng cao sau khi tải file lên', 'type': 'bool',
     'default': True,
     'help': 'Chạy nền: chấm điểm OCR từng trang, sửa trang lỗi bằng model vision, lập chỉ mục theo Điều/mục, tóm tắt sẵn.'},
    {'key': 'files.pipeline.concurrency', 'label': 'Số file xử lý nền cùng lúc', 'type': 'int', 'min': 1, 'max': 8,
     'default': 2},
    {'key': 'files.ocr.text_layer_first', 'label': 'PDF có sẵn chữ: đọc trực tiếp, chỉ OCR trang lỗi', 'type': 'bool',
     'default': True,
     'help': 'Áp dụng khi engine trích xuất là OCR (PaddleOCR-VL, Docling, ...). Trang có lớp chữ đạt điểm thì không gửi OCR.'},
    {'key': 'files.ocr.min_score', 'label': 'Điểm OCR tối thiểu (%)', 'type': 'int', 'min': 0, 'max': 100,
     'default': 80,
     'help': 'Trang có điểm thấp hơn bị coi là OCR kém. Điểm = tỉ lệ từ đọc được (âm tiết tiếng Việt hợp lệ), trừ ký tự rác.'},
    {'key': 'files.repair.enable', 'label': 'Sửa trang OCR kém bằng model vision', 'type': 'bool',
     'default': True},
    {'key': 'files.repair.model', 'label': 'Model vision sửa OCR', 'type': 'text',
     'default': '',
     'help': 'ID model của nhà cung cấp (vd qwen3-vl). Trống = model của tracker (TRACKER_MODEL). Model phải nhận được ảnh.'},
    {'key': 'files.repair.ocr_hint', 'label': 'Gửi kèm chữ OCR khi sửa bằng vision', 'type': 'bool', 'default': False,
     'help': 'Tắt (khuyến nghị): model chỉ nhìn ảnh. Bật thì model dễ chép lại lỗi của OCR, kể cả chữ Trung bịa ra.'},
    {'key': 'files.repair.concurrency', 'label': 'Số trang sửa song song', 'type': 'int', 'min': 1, 'max': 16,
     'default': 4},
    {'key': 'files.repair.max_pages', 'label': 'Số trang tối đa được sửa mỗi file', 'type': 'int', 'min': 0, 'max': 5000,
     'default': 300},
    {'key': 'files.chunk.by_structure', 'label': 'Lập chỉ mục theo Chương/Mục/Điều', 'type': 'bool',
     'default': True,
     'help': 'Mỗi đoạn tìm kiếm là một Điều/mục, kèm đường dẫn "Chương › Điều" và số trang.'},
    {'key': 'files.chunk.max_chars', 'label': 'Độ dài tối đa một đoạn tìm kiếm (ký tự)', 'type': 'int', 'min': 500,
     'max': 8000, 'default': 2500},
    {'key': 'files.summary.precompute', 'label': 'Tóm tắt sẵn file lớn sau khi tải lên', 'type': 'bool',
     'default': True},
    {'key': 'files.summary.precompute_min_chars', 'label': 'Tóm tắt sẵn khi file dài từ (ký tự)', 'type': 'int',
     'min': 0, 'max': 10_000_000, 'default': 30_000},
    {'key': 'files.summary.model', 'label': 'Model tóm tắt nền', 'type': 'text',
     'default': '',
     'help': 'ID model của nhà cung cấp cho tóm tắt chạy nền. Trống = model của tracker.'},
    {'key': 'files.summary.chat_model', 'label': 'Model tóm tắt khi chat (summarize_chat_file)', 'type': 'text',
     'default': '', 'help': 'ID model trong NCS AI. Trống = model đang trả lời.'},
    {'key': 'files.summary.segment_chars', 'label': 'Số ký tự mỗi phần khi tóm tắt', 'type': 'int', 'min': 5000,
     'max': 120_000, 'default': 40_000},
    {'key': 'files.summary.concurrency', 'label': 'Số phần tóm tắt song song', 'type': 'int', 'min': 1, 'max': 16,
     'default': 6},
    {'key': 'files.summary.max_chars', 'label': 'Độ dài bản tóm tắt trả về (ký tự)', 'type': 'int', 'min': 2000,
     'max': 100_000, 'default': 16_000},
    {'key': 'files.summary.disable_thinking', 'label': 'Tắt thinking khi tóm tắt / sửa OCR', 'type': 'bool',
     'default': True, 'help': 'Gửi enable_thinking=false (Qwen3 trên vLLM). Nhanh hơn 2-3 lần.'},
    {'key': 'files.attach.inline_max_chars', 'label': 'Đưa nguyên văn file đính kèm tới (ký tự)', 'type': 'int',
     'min': 0, 'max': 500_000, 'default': 45_000,
     'help': 'Tổng ký tự các file của một tin nhắn. Nhỏ hơn mức này thì model đọc ngay, không cần gọi tool. 0 = tắt.'},
    {'key': 'files.read_budget_chars', 'label': 'Giới hạn đọc file trong một câu trả lời (ký tự)', 'type': 'int',
     'min': 0, 'max': 5_000_000, 'default': 60_000,
     'help': 'Tổng ký tự view_file và query_chat_files trả về trong một câu trả lời. 0 = không giới hạn.'},
    {'key': 'files.query_max_chars', 'label': 'Ký tự tối đa mỗi lần tìm trong file', 'type': 'int', 'min': 1000,
     'max': 200_000, 'default': 12_000},
    {'key': 'files.verify.enable', 'label': 'Kiểm tra số liệu trong câu trả lời với tài liệu', 'type': 'bool',
     'default': True,
     'help': 'Con số, ngày tháng trong câu trả lời không có trong phần tài liệu đã đọc sẽ được liệt kê cảnh báo.'},
    {'key': 'files.page_image_max_side', 'label': 'Cạnh dài ảnh trang PDF gửi model (px)', 'type': 'int', 'min': 600,
     'max': 3000, 'default': 1600},
]  # fmt: skip
for _setting in FILE_SETTINGS:
    _setting['group'] = FILE_SETTINGS_GROUP[0]
FILE_SETTINGS_BY_KEY = {s['key']: s for s in FILE_SETTINGS}

_CACHE_SECONDS = 5
_cache: tuple[float, dict] = (0.0, {})


async def file_settings() -> dict:
    """Every file setting, database value over default, cached a few seconds."""
    global _cache
    if time.monotonic() < _cache[0]:
        return _cache[1]
    from open_webui.models.config import Config

    stored = await Config.get_many(*FILE_SETTINGS_BY_KEY)
    values = {key: stored[key] if stored.get(key) is not None else s['default'] for key, s in FILE_SETTINGS_BY_KEY.items()}
    _cache = (time.monotonic() + _CACHE_SECONDS, values)
    return values


def reset_file_settings_cache() -> None:
    global _cache
    _cache = (0.0, {})


def coerce(setting: dict, value):
    """Validated value for one setting, or ValueError with a readable message."""
    if setting['type'] == 'bool':
        return value if isinstance(value, bool) else str(value).lower() in ('1', 'true', 'on', 'yes')
    if setting['type'] == 'int':
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{setting["label"]}: phải là số nguyên')
        low, high = setting.get('min'), setting.get('max')
        if (low is not None and number < low) or (high is not None and number > high):
            raise ValueError(f'{setting["label"]}: phải trong khoảng {low}–{high}')
        return number
    value = '' if value is None else str(value).strip()
    if len(value) > 500:
        raise ValueError(f'{setting["label"]}: quá dài')
    return value
