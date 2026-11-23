"""Buttons of the bot: the keyboard under the input (navigation, always shown) and buttons under messages.

Callback data stays under Telegram's 64 bytes: ids of channels (uuid) fit; long lists (models) use an index
into the list saved in the chat session. Every id coming back from a button is checked again before use.
"""

from typing import Optional

# Keyboard under the input
ASK_DIRECT = '💬 Hỏi trực tiếp'
ASK_CHANNEL = '📁 Hỏi trong kênh'
NEW_CHAT = '🆕 Cuộc trò chuyện mới'
SETTINGS = '⚙️ Cài đặt'
OVERVIEW = '📊 Tiến độ tổng quan'
OVERDUE = '⏰ Việc quá hạn'
MINE = '👤 Việc của tôi'
EXPORT = '📄 Xuất báo cáo'
CHANGE_CHANNEL = '🔄 Đổi kênh'
BACK_DIRECT = '↩️ Về hỏi trực tiếp'

PRESETS = {
    OVERVIEW: 'Tóm tắt tiến độ kênh này: việc đã xong, việc đang làm, việc trễ hạn và các vướng mắc cần xử lý.',
    OVERDUE: 'Liệt kê các việc đang quá hạn trong kênh này: việc gì, ai phụ trách, hạn khi nào, đã trễ bao lâu.',
    MINE: 'Liệt kê các việc tôi đang phụ trách trong kênh này, nêu việc nào sắp đến hạn hoặc đã quá hạn.',
}
EXPORT_PROMPT = (
    'Lập báo cáo tiến độ đầy đủ của kênh này (việc đã xong, đang làm, trễ hạn, vướng mắc, theo từng người phụ '
    'trách) và xuất ra file {label}: dùng build_project_report rồi export_project_report với format={fmt}.'
)
EXPORT_FORMATS = {'pdf': 'PDF', 'docx': 'Word', 'xlsx': 'Excel'}
FILE_ACTIONS = {
    'sum': ('📝 Tóm tắt', 'Tóm tắt nội dung chính của file này.'),
    'rev': ('🔍 Rà soát', 'Rà soát file này: nêu các điểm bất thường, thiếu sót, mâu thuẫn và rủi ro cần lưu ý.'),
}


def main_keyboard() -> dict:
    return {
        'keyboard': [[{'text': ASK_DIRECT}, {'text': ASK_CHANNEL}], [{'text': NEW_CHAT}, {'text': SETTINGS}]],
        'resize_keyboard': True,
        'is_persistent': True,
        'input_field_placeholder': 'Nhập câu hỏi cho AI…',
    }


def channel_keyboard(channel_name: str) -> dict:
    return {
        'keyboard': [
            [{'text': OVERVIEW}, {'text': OVERDUE}],
            [{'text': MINE}, {'text': EXPORT}],
            [{'text': CHANGE_CHANNEL}, {'text': BACK_DIRECT}],
        ],
        'resize_keyboard': True,
        'is_persistent': True,
        'input_field_placeholder': f'Hỏi trong #{channel_name}'[:64],
    }


def remove_keyboard() -> dict:
    return {'remove_keyboard': True}


def inline(rows: list[list[dict]]) -> dict:
    return {'inline_keyboard': [row for row in rows if row]}


def url_button(text: str, url: str) -> Optional[dict]:
    return {'text': text, 'url': url} if url and url.startswith(('http://', 'https://')) else None


def link_account(url: str) -> dict:
    button = url_button('🔗 Liên kết tài khoản', url)
    return inline([[button]] if button else [])


def stop(key: str) -> dict:
    return inline([[{'text': '⏹ Dừng', 'callback_data': f'stop:{key}'}]])


def answer(kind: str, url: str, files_enabled: bool) -> dict:
    rows = []
    if files_enabled:
        rows.append([{'text': '📄 PDF', 'callback_data': 'x:pdf'}, {'text': '📝 Word', 'callback_data': 'x:docx'}])
    second = []
    if kind == 'chat':
        second.append({'text': '🔁 Trả lời lại', 'callback_data': 'rg'})
        button = url_button('🌐 Mở trên web', url)
    else:
        button = url_button('💬 Mở trong kênh', url)
    if button:
        second.append(button)
    rows.append(second)
    return inline(rows)


def error_buttons(url: str) -> dict:
    button = url_button('🌐 Mở trên web', url)
    return inline([[{'text': '🔁 Thử lại', 'callback_data': 'rg'}], [button] if button else []])


def channel_picker(channels: list, recent: list, page: int, per_page: int = 6) -> dict:
    rows = []
    if page == 0 and recent:
        rows.append([{'text': f'⭐ #{c.name}'[:40], 'callback_data': f'ch:{c.id}'} for c in recent[:2]])
    shown = {c.id for c in recent[:2]} if page == 0 else set()
    rest = [c for c in channels if c.id not in shown]
    pages = max(1, (len(rest) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    items = rest[page * per_page : (page + 1) * per_page]
    for start in range(0, len(items), 2):
        rows.append([{'text': f'#{c.name}'[:40], 'callback_data': f'ch:{c.id}'} for c in items[start : start + 2]])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append({'text': '‹ Trước', 'callback_data': f'chp:{page - 1}'})
        nav.append({'text': f'{page + 1}/{pages}', 'callback_data': 'noop'})
        if page < pages - 1:
            nav.append({'text': 'Sau ›', 'callback_data': f'chp:{page + 1}'})
        rows.append(nav)
    return inline(rows)


def model_picker(models: list[dict], current: Optional[str]) -> dict:
    rows = []
    for index, model in enumerate(models[:40]):
        mark = '✓ ' if model['id'] == current else ''
        rows.append([{'text': f'{mark}{model.get("name") or model["id"]}'[:60], 'callback_data': f'm:{index}'}])
    return inline(rows)


def settings_menu(reports_on: bool, can_ask: bool) -> dict:
    rows = []
    first = []
    if can_ask:
        first.append({'text': '🤖 Đổi model', 'callback_data': 'mdl'})
    if reports_on:
        first.append({'text': '📬 Báo cáo nhận tại đây', 'callback_data': 'rcv'})
    rows.append(first)
    rows.append([{'text': '❓ Hướng dẫn', 'callback_data': 'help'}, {'text': '🔓 Hủy liên kết', 'callback_data': 'unl'}])
    return inline(rows)


def confirm_unlink() -> dict:
    return inline([[{'text': 'Hủy liên kết', 'callback_data': 'unl:yes'}, {'text': 'Không', 'callback_data': 'unl:no'}]])


def export_formats() -> dict:
    return inline([[{'text': f'📄 {label}' if fmt == 'pdf' else ('📝 ' if fmt == 'docx' else '📊 ') + label, 'callback_data': f'rep:{fmt}'}
                    for fmt, label in EXPORT_FORMATS.items()]])


def file_actions() -> dict:
    return inline([[{'text': label, 'callback_data': f'fa:{key}'} for key, (label, _) in FILE_ACTIONS.items()]
                   + [{'text': '❓ Tôi sẽ hỏi', 'callback_data': 'fa:wait'}]])


def strip_urls(markup: Optional[dict]) -> Optional[dict]:
    """The same buttons without the URL ones (when Telegram refuses the web address, e.g. a local address)."""
    if not markup or 'inline_keyboard' not in markup:
        return markup
    rows = [[b for b in row if 'url' not in b] for row in markup['inline_keyboard']]
    rows = [row for row in rows if row]
    return {'inline_keyboard': rows} if rows else None
