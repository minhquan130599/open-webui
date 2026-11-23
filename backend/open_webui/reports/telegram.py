"""Sending scheduled reports by Telegram.

The Bot API calls live in open_webui.telegram.api and the bot's updates (linking chats and accounts, questions)
are read by open_webui.telegram.bot; this module keeps the report-side helpers.
"""

from typing import Optional

from open_webui.reports.models import TelegramLinks
from open_webui.telegram.api import MESSAGE_CHARS, TelegramError, bot_info, call, gone_chat, split_message  # noqa: F401
from open_webui.telegram.api import send_message


async def send(token: str, chat_id: str, html_text: str, button: Optional[tuple[str, str]] = None) -> None:
    """Send a report (Telegram HTML), split into several messages when long; the button goes on the last one."""
    parts = split_message(html_text)
    for index, part in enumerate(parts):
        markup = None
        if button and index == len(parts) - 1 and button[1].startswith(('http://', 'https://')):
            markup = {'inline_keyboard': [[{'text': button[0], 'url': button[1]}]]}
        try:
            await send_message(token, chat_id, part, markup)
        except TelegramError as error:
            if gone_chat(error):
                await TelegramLinks.forget_chat(str(chat_id))
                raise TelegramError(f'Chat Telegram không còn nhận được tin (bot đã bị xóa / chặn): {error}')
            raise
