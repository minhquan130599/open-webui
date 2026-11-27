"""Buttons of the bot: the keyboard under the input (choose a channel, settings) and buttons under messages.

Callback data stays under Telegram's 64 bytes: ids of channels (uuid) fit. Every id coming back from a button is
checked again before use.
"""

from typing import Optional

# Keyboard under the input
CHOOSE_CHANNEL = '📁 Chọn kênh'
CHANGE_CHANNEL = '🔄 Đổi kênh'
SETTINGS = '⚙️ Cài đặt'

# Buttons of the keyboard shown before the bot stopped chatting with the AI: still on the screen of users who have
# not written since. Pressing one shows the new keyboard instead of posting the button's text into a channel.
OLD_BUTTONS = (
    '💬 Hỏi trực tiếp',
    '📁 Hỏi trong kênh',
    '🆕 Cuộc trò chuyện mới',
    '📊 Tiến độ tổng quan',
    '⏰ Việc quá hạn',
    '👤 Việc của tôi',
    '📄 Xuất báo cáo',
    '↩️ Về hỏi trực tiếp',
)


def main_keyboard() -> dict:
    return {
        'keyboard': [[{'text': CHOOSE_CHANNEL}, {'text': SETTINGS}]],
        'resize_keyboard': True,
        'is_persistent': True,
        'input_field_placeholder': 'Chọn kênh để gửi tin nhắn',
    }


def channel_keyboard(channel_name: str) -> dict:
    return {
        'keyboard': [[{'text': CHANGE_CHANNEL}, {'text': SETTINGS}]],
        'resize_keyboard': True,
        'is_persistent': True,
        'input_field_placeholder': f'Gửi vào #{channel_name}'[:64],
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


def settings_menu(can_post: bool) -> dict:
    rows = []
    if can_post:
        rows.append([{'text': '📁 Chọn kênh', 'callback_data': 'chp:0'}])
    rows.append([{'text': '❓ Hướng dẫn', 'callback_data': 'help'}, {'text': '🔓 Hủy liên kết', 'callback_data': 'unl'}])
    return inline(rows)


def confirm_unlink() -> dict:
    return inline([[{'text': 'Hủy liên kết', 'callback_data': 'unl:yes'}, {'text': 'Không', 'callback_data': 'unl:no'}]])


def strip_urls(markup: Optional[dict]) -> Optional[dict]:
    """The same buttons without the URL ones (when Telegram refuses the web address, e.g. a local address)."""
    if not markup or 'inline_keyboard' not in markup:
        return markup
    rows = [[b for b in row if 'url' not in b] for row in markup['inline_keyboard']]
    rows = [row for row in rows if row]
    return {'inline_keyboard': rows} if rows else None
