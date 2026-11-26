"""Keep a tool-loop request inside the answering model's context window.

The window and the estimate come from the file settings (model.context_tokens, model.answer_reserve_tokens,
model.chars_per_token, model.image_tokens), so a model with a larger context only needs those values changed.

When the messages would not fit, the oldest page images are dropped first, then the oldest tool results are cut
to a short stub, then the newest tool result is shortened; the user's question, the system prompt and the
model's own messages are never touched. A request that is nearly full is turned into a final-answer request, so
the answer is written from what was read instead of failing on the next call.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)

TRIMMED_NOTE = '[Phần kết quả này đã lược bớt để vừa ngữ cảnh của model: {chars} ký tự]'
IMAGE_NOTE = '[Ảnh trang đã gửi ở bước trước, bỏ bớt để vừa ngữ cảnh của model]'
STUB_CHARS = 300


def limits(settings: dict) -> dict:
    """Token budget of one request and how to estimate tokens."""
    return {
        'budget': max(settings['model.context_tokens'] - settings['model.answer_reserve_tokens'], 1000),
        'chars_per_token': max(settings['model.chars_per_token'], 1),
        'image_tokens': settings['model.image_tokens'],
    }


def _is_image(part: Any) -> bool:
    return isinstance(part, dict) and part.get('type') in ('image_url', 'image', 'input_image')


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(
            str(part.get('text') or '') if isinstance(part, dict) else str(part) for part in content if not _is_image(part)
        )
    return '' if content is None else str(content)


def message_tokens(message: dict, chars_per_token: int, image_tokens: int) -> int:
    content = message.get('content')
    images = sum(_is_image(part) for part in content) if isinstance(content, list) else 0
    chars = len(_text_of(content)) + len(str(message.get('tool_calls') or ''))
    return 4 + chars // chars_per_token + images * image_tokens


def estimate(messages: list[dict], chars_per_token: int, image_tokens: int, tools: Any = None) -> int:
    return sum(message_tokens(m, chars_per_token, image_tokens) for m in messages) + len(str(tools or '')) // chars_per_token


def _is_tool_images(message: dict) -> bool:
    """The user message the tool loop adds to carry images returned by tools."""
    content = message.get('content')
    return message.get('role') == 'user' and isinstance(content, list) and any(_is_image(p) for p in content) and any(
        isinstance(p, dict) and 'images from the tool results' in str(p.get('text') or '') for p in content
    )


def fit(messages: list[dict], budget: int, chars_per_token: int, image_tokens: int, tools: Any = None) -> tuple[list[dict], int]:
    """(messages that fit the budget, tokens removed). Only tool results and tool images are shortened."""
    total = estimate(messages, chars_per_token, image_tokens, tools)
    if total <= budget:
        return messages, 0
    before = total
    messages = [dict(m) for m in messages]
    tool_results = [i for i, m in enumerate(messages) if m.get('role') == 'tool']
    newest = tool_results[-1] if tool_results else None

    # 1. Page images carried for tools, oldest first.
    for i, message in enumerate(messages):
        if total <= budget:
            break
        if _is_tool_images(message):
            old = message_tokens(message, chars_per_token, image_tokens)
            messages[i] = {**message, 'content': IMAGE_NOTE}
            total -= old - message_tokens(messages[i], chars_per_token, image_tokens)

    # 2. Older tool results become short stubs, oldest first.
    for i in tool_results:
        if total <= budget:
            break
        if i == newest:
            continue
        text = _text_of(messages[i].get('content'))
        if len(text) <= STUB_CHARS * 2:
            continue
        old = message_tokens(messages[i], chars_per_token, image_tokens)
        messages[i] = {**messages[i], 'content': text[:STUB_CHARS] + '\n' + TRIMMED_NOTE.format(chars=len(text) - STUB_CHARS)}
        total -= old - message_tokens(messages[i], chars_per_token, image_tokens)

    # 3. The newest tool result is shortened to what still fits.
    if total > budget and newest is not None:
        text = _text_of(messages[newest].get('content'))
        keep = max(len(text) - (total - budget) * chars_per_token - 200, STUB_CHARS)
        if keep < len(text):
            old = message_tokens(messages[newest], chars_per_token, image_tokens)
            messages[newest] = {**messages[newest], 'content': text[:keep] + '\n' + TRIMMED_NOTE.format(chars=len(text) - keep)}
            total -= old - message_tokens(messages[newest], chars_per_token, image_tokens)

    log.info('Context fit: %s -> %s estimated tokens (budget %s)', before, total, budget)
    return messages, before - total


EARLIER_RESULT_NOTE = (
    '[Kết quả của {name}({args}) ở lượt trước ({chars} ký tự) không còn trong ngữ cảnh; những gì cần đã được dùng '
    'trong câu trả lời của lượt đó. Cần lại nội dung thì gọi lại tool.]'
)


def clear_earlier_tool_outputs(messages: list[dict]) -> int:
    """Tool results of earlier turns (before the last user message) replaced by a one-line note; chars removed.

    What the model took from them is in its answers, which stay; the question, the answers and which tool was called
    with what stay too. A follow-up that needs the text again reads it again (a tool call, not generation), so the
    context holds the current turn instead of every page read since the chat began. Messages are replaced, never
    changed in place (they come from the stored chat).
    """
    last_user = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
    removed = 0
    for i in range(max(last_user, 0)):
        message = messages[i]
        output = message.get('output')
        if message.get('role') != 'assistant' or not isinstance(output, list):
            continue
        calls = {item.get('call_id'): item for item in output if isinstance(item, dict) and item.get('type') == 'function_call'}
        kept = []
        for item in output:
            if isinstance(item, dict) and item.get('type') == 'function_call_output':
                text = _text_of(item.get('output'))
                if len(text) > STUB_CHARS * 2:
                    call = calls.get(item.get('call_id')) or {}
                    args = str(call.get('arguments') or '')
                    note = EARLIER_RESULT_NOTE.format(
                        name=call.get('name') or 'tool',
                        args=args if len(args) <= 200 else args[:200] + '…',
                        chars=len(text),
                    )
                    item = {**item, 'output': [{'type': 'input_text', 'text': note}]}
                    removed += len(text)
            kept.append(item)
        messages[i] = {**message, 'output': kept}
    return removed


def is_context_error(detail: Any) -> bool:
    """The provider refused the request because it is longer than the model's context."""
    text = str(detail or '').lower()
    return any(
        key in text
        for key in ('maximum context length', 'max_model_len', 'context length', 'context_length', 'too many tokens',
                    'prompt is too long', 'input is too long', 'longer than the maximum')
    )
