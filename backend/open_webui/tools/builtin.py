"""
Built-in tools for Open WebUI.

These tools are automatically available when native function calling is enabled.

IMPORTANT: DO NOT IMPORT THIS MODULE DIRECTLY IN OTHER PARTS OF THE CODEBASE.
"""

import asyncio
import logging
import re
import time
from typing import Literal, Optional
from urllib.parse import unquote

from fastapi import HTTPException, Request

from open_webui.env import (
    KNOWLEDGE_GREP_MAX_MATCHES,
    TERMINAL_ATTACHMENTS_DIR,
    VIEW_FILE_DEFAULT_MAX_CHARS,
    VIEW_FILE_MAX_CHARS,
)
from open_webui.events import EVENTS, publish_event
from open_webui.models.channels import Channel, ChannelMember, Channels
from open_webui.models.chats import Chats, chat_search_content_query, chat_search_terms
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.memories import Memories
from open_webui.models.messages import Message, Messages
from open_webui.models.notes import NotePageConflict, Notes, get_content_files, get_page, matching_page
from open_webui.models.users import UserModel, Users
from open_webui.retrieval.utils import filter_source_metadata, get_content_from_url
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.routers.images import (
    CreateImageForm,
    EditImageForm,
    get_image_data_url,
    image_edits,
    image_generations,
)
from open_webui.routers.memories import (
    AddMemoryForm,
    ListMemoryPathsForm,
    MemoryUpdateModel,
    ReadMemoryPathForm,
    SearchMemoriesForm,
    UpdateMemoriesForm,
    update_memory_by_id,
)
from open_webui.routers.memories import (
    add_memory as _add_memory,
)
from open_webui.routers.memories import (
    list_memory_paths as _list_memory_paths,
)
from open_webui.routers.memories import (
    read_memory_path as _read_memory_path,
)
from open_webui.routers.memories import (
    search_memories as _search_memories,
)
from open_webui.routers.memories import (
    update_memories as _update_memories,
)
from open_webui.routers.retrieval import search_web as _search_web
from open_webui.socket.main import close_changed_note_documents, sio
from open_webui.tools.knowledge_fs import kb_exec  # noqa: F401 — re-exported
from open_webui.utils.access_control.files import has_access_to_note_files
from open_webui.utils.chat_id import CHANNEL_CHAT_ID_PREFIX, is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.notifications import notify_target
from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.skill_files import SkillFile, SkillFileOperation, skill_content_page, skill_overview
from open_webui.utils.terminals import get_terminal_file, upload_files_to_terminal

log = logging.getLogger(__name__)

MAX_KNOWLEDGE_BASE_SEARCH_ITEMS = 10_000


async def _has_write_access_to_note(note, user_id: str) -> bool:
    if note.user_id == user_id:
        return True

    from open_webui.models.access_grants import AccessGrants

    user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
    return await AccessGrants.has_access(
        user_id=user_id,
        resource_type='note',
        resource_id=note.id,
        permission='write',
        user_group_ids=set(user_group_ids),
    )


async def _emit_note_updated(request: Request, user: dict, note) -> None:
    await sio.emit('events:note', note.model_dump(), to=f'note:{note.id}')
    await publish_event(
        request,
        EVENTS.NOTE_UPDATED,
        actor=user,
        subject_id=note.id,
        data={'title': note.title},
    )


async def _has_read_access_to_file(
    file,
    user: dict,
    model_knowledge: Optional[list[dict]] = None,
) -> bool:
    """Check if a user can read a file via ownership, admin role, model attachment, or access grants."""
    user_id = user.get('id')
    user_role = user.get('role', 'user')
    if file.user_id == user_id or user_role == 'admin':
        return True
    if model_knowledge and any(item.get('type') == 'file' and item.get('id') == file.id for item in model_knowledge):
        return True
    from open_webui.utils.access_control.files import has_access_to_file

    return await has_access_to_file(
        file_id=file.id,
        access_type='read',
        user=UserModel(**user),
    )


# Time utilities


async def notify(
    message: str,
    target: str = '',
    title: str = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a notification to the user's configured notification target.

    :param message: Notification body.
    :param target: Optional target id or name. Empty uses the default target.
    :param title: Optional notification title.
    """
    user_id = (__user__ or {}).get('id')
    if not user_id:
        return 'Notification failed: user not found.'

    app_name = getattr(getattr(__request__, 'app', None), 'state', None)
    # LICENSE covers this Open WebUI notification identifier.
    # Do not alter, remove, obscure, or replace it except as LICENSE permits:
    # https://docs.openwebui.com/license.
    app_name = getattr(app_name, 'WEBUI_NAME', 'Open WebUI')
    try:
        result = await notify_target(user_id, message, target=target, title=title, app_name=app_name)
        return f'Notification sent to {result.get("target_id")}.'
    except Exception as e:
        return f'Notification failed: {e}'


async def get_current_timestamp(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp in seconds.

    :return: JSON with current_timestamp (seconds), current_iso (UTC ISO format), and user_local_iso (user's local time)
    """
    try:
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(datetime.timezone.utc)
        result = {
            'current_timestamp': int(now.timestamp()),
            'current_iso': now.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                user_tz = ZoneInfo(tz_name)
                user_now = now.astimezone(user_tz)
                result['user_local_iso'] = user_now.isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'get_current_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def calculate_timestamp(
    days_ago: int = 0,
    weeks_ago: int = 0,
    months_ago: int = 0,
    years_ago: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp, optionally adjusted by days, weeks, months, or years.
    Use this to calculate timestamps for date filtering in search functions.
    Examples: "last week" = weeks_ago=1, "3 days ago" = days_ago=3, "a year ago" = years_ago=1

    :param days_ago: Number of days to subtract from current time (default: 0)
    :param weeks_ago: Number of weeks to subtract from current time (default: 0)
    :param months_ago: Number of months to subtract from current time (default: 0)
    :param years_ago: Number of years to subtract from current time (default: 0)
    :return: JSON with current_timestamp and calculated_timestamp (both in seconds)
    """
    try:
        import datetime

        from dateutil.relativedelta import relativedelta

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())

        # Calculate the adjusted time
        total_days = days_ago + (weeks_ago * 7)
        adjusted = now - datetime.timedelta(days=total_days)

        # Months and years need calendar arithmetic; February refuses to be 30 days.
        if months_ago > 0 or years_ago > 0:
            adjusted = adjusted - relativedelta(months=months_ago, years=years_ago)

        adjusted_ts = int(adjusted.timestamp())

        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except ImportError:
        # Fallback without dateutil
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())
        total_days = days_ago + (weeks_ago * 7) + (months_ago * 30) + (years_ago * 365)
        adjusted = now - datetime.timedelta(days=total_days)
        adjusted_ts = int(adjusted.timestamp())
        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'calculate_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Web search tools


async def search_web(
    query: str,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the public web for information. Best for current events, external references,
    or topics not covered in internal documents.

    :param query: The search query to look up
    :param count: Number of results to return (default: admin-configured value)
    :return: JSON with search results containing title, link, and snippet for each result
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        engine = await Config.get('web.search.engine')
        user = UserModel(**__user__) if __user__ else None

        configured = await Config.get('web.search.result_count')
        max_count = 5 if configured is None else configured
        count = max(1, min(count, max_count)) if count is not None else max_count

        results = await _search_web(__request__, engine, query, user)

        # Limit results
        results = results[:count] if results else []

        return JSONCodec.dumps(
            [{'title': r.title, 'link': r.link, 'snippet': r.snippet} for r in results],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_web error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def fetch_url(
    url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Fetch and extract the main text content from a web page URL.

    :param url: The URL to fetch content from
    :return: The extracted text content from the page
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        content, _ = await get_content_from_url(__request__, url)

        # Truncate if configured (WEB_FETCH_MAX_CONTENT_LENGTH)
        # Guard: content may be None if the web loader silently failed
        if content is not None:
            max_length = await Config.get('web.fetch.max_content_length')
            if max_length and max_length > 0 and len(content) > max_length:
                content = content[:max_length] + '\n\n[Content truncated...]'
        else:
            content = ''

        return content
    except Exception as e:
        log.warning(f'fetch_url error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Image generation tools


async def generate_image(
    prompt: str,
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Generate an image based on a text prompt.

    :param prompt: A detailed description of the image to generate
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Image URLs and, when a terminal is selected, terminal_path for display_file or further processing.
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_generations(
            request=__request__,
            form_data=CreateImageForm(prompt=prompt, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        await upload_files_to_terminal(
            __request__, user, images, __metadata__ or {}, {'__event_call__': __event_call__}
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': (
                        'The image is already visible in chat. Use terminal_path with terminal tools or '
                        'display_file when available to continue the requested task.'
                    ),
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'generate_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Terminal deliverables

ATTACH_TERMINAL_FILE_MAX_BYTES = 100 * 1024 * 1024


# Files attached to each reply while it is written: reply message id -> {file name: file id}. Lets display_file
# open a file the model just attached when it names it (instead of looking for that name in the terminal).
REPLY_FILES: dict[str, dict[str, str]] = {}


def remember_reply_file(message_id: Optional[str], name: str, file_id: str) -> None:
    if not message_id:
        return
    REPLY_FILES.setdefault(message_id, {})[name] = file_id
    while len(REPLY_FILES) > 500:
        REPLY_FILES.pop(next(iter(REPLY_FILES)))


def known_file_id(path: str, metadata: dict) -> Optional[str]:
    """The id of a file named `path` (a bare file name) that this reply attached or the chat has attached."""
    name = str(path or '').strip()
    if not name or '/' in name or '\\' in name:
        return None  # a path is a terminal path
    found = (REPLY_FILES.get(metadata.get('message_id') or '') or {}).get(name)
    if found:
        return found
    for item in metadata.get('files') or []:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        file = item.get('file') if isinstance(item.get('file'), dict) else {}
        names = {item.get('name'), file.get('filename'), (file.get('meta') or {}).get('name')}
        if name in names:
            return item['id']
    return None


async def attach_to_reply(
    request: Request,
    user: UserModel,
    chat_id: Optional[str],
    message_id: Optional[str],
    event_emitter,
    name: str,
    data: bytes,
    content_type: str,
) -> dict:
    """Save bytes as an Open WebUI file and attach it to the assistant reply (chat or channel message).

    Returns the attachment entry. In a channel the file is linked to the channel so every member can open it.
    """
    import io

    from fastapi import UploadFile

    from open_webui.routers.files import upload_file_handler

    in_channel = isinstance(chat_id, str) and chat_id.startswith(CHANNEL_CHAT_ID_PREFIX)
    upload_metadata = (
        {'channel_id': chat_id.removeprefix(CHANNEL_CHAT_ID_PREFIX), 'message_id': message_id} if in_channel else {}
    )
    file_item = await upload_file_handler(
        request,
        file=UploadFile(file=io.BytesIO(data), filename=name, headers={'content-type': content_type}),
        metadata=upload_metadata,
        process=False,
        user=user,
    )
    if not file_item:
        raise ValueError(f'Could not save {name}')
    remember_reply_file(message_id, name, file_item.id)

    entry = {
        'type': 'image' if content_type.startswith('image/') else 'file',
        'id': file_item.id,
        'url': request.app.url_path_for('get_file_content_by_id', id=file_item.id),
        'name': name,
        'size': len(data),
        'content_type': content_type,
    }
    files = [entry]
    if is_saved_chat_id(chat_id) and message_id:
        await Chats.insert_chat_files(chat_id=chat_id, message_id=message_id, file_ids=[file_item.id], user_id=user.id)
        # The UI replaces the message's file list with this event, so send the full list.
        db_files = await Chats.add_message_files_by_id_and_message_id(chat_id, message_id, files)
        if db_files is not None:
            files = db_files
    if event_emitter:
        await event_emitter({'type': 'chat:message:files', 'data': {'files': files}})
    return entry


async def attach_terminal_file(
    path: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Deliver a file you created in the terminal (PDF, DOCX, XLSX, PPTX, CSV, image, ZIP...) to the user as a
    downloadable attachment on your reply; it is saved in Open WebUI and stays available after the terminal changes.
    Create the file with terminal commands first. To make a PDF from an office file, run
    `soffice --headless --convert-to pdf --outdir <dir> <file.docx>`. Call once per file you deliver,
    then tell the user the file is attached instead of pasting its content or a terminal path.

    :param path: Path of the file in the terminal (absolute, or relative to the current working directory)
    :return: JSON with the saved file id, name, size and download URL
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        import mimetypes
        import posixpath

        user = UserModel(**__user__)
        data = await get_terminal_file(
            __request__, user, __metadata__ or {}, path, max_bytes=ATTACH_TERMINAL_FILE_MAX_BYTES
        )
        name = posixpath.basename(path.rstrip('/')) or 'file'
        content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        entry = await attach_to_reply(
            __request__, user, __chat_id__, __message_id__, __event_emitter__, name, data, content_type
        )
        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'{name} is attached to your reply; do not repeat its content or paste a link.',
                'file': {k: entry[k] for k in ('id', 'name', 'size', 'content_type', 'url')},
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'attach_terminal_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def edit_image(
    prompt: str,
    image_urls: list[str],
    size: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Transform one or more existing images according to a text prompt.
    Supports targeted edits such as adding, removing, replacing, inpainting, extending, or compositing image content.

    :param prompt: A description of the transformation to apply to the provided images
    :param image_urls: Source image URLs or absolute file paths to modify or use as composition inputs
    :param size: Optional output size in WIDTHxHEIGHT pixels (e.g. "1536x1024"), supported by the configured image model.
        Omit to use the configured default.
    :return: Image URLs and, when a terminal is selected, terminal_path for display_file or further processing.
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        resolved_images = []
        for url in image_urls:
            if url.startswith('/') and not url.startswith('/api/v1/files/'):
                data = await get_terminal_file(__request__, user, __metadata__ or {}, url, max_bytes=50 * 1024 * 1024)
                url = await asyncio.to_thread(get_image_data_url, data)
            resolved_images.append(url)
        images = await image_edits(
            request=__request__,
            form_data=EditImageForm(prompt=prompt, image=resolved_images, size=size),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        await upload_files_to_terminal(
            __request__, user, images, __metadata__ or {}, {'__event_call__': __event_call__}
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': (
                        'The edited image is already visible in chat. Use terminal_path with terminal tools or '
                        'display_file when available to continue the requested task.'
                    ),
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'edit_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# User input tools


async def ask_user(
    questions: list[dict],
    allow_other: bool = True,
    timeout_ms: int = 120_000,
    __event_call__: callable = None,
) -> str:
    """
    Ask the user clarifying questions before continuing.
    Use this when the next step depends on user intent, preference, or a tradeoff that cannot be inferred safely.

    :param questions: 1-3 question objects, each with id, header, question, and 2-3 options. Each option needs label and description.
        List the option you recommend first; the UI labels the first option Recommended.
    :param allow_other: Whether users may enter a free-form answer instead of choosing one of the options
    :param timeout_ms: How long the browser should keep the prompt open before cancelling it
    :return: JSON with status and answers keyed by question id
    """
    try:
        if not isinstance(questions, list):
            raise ValueError('ask_user questions must be an array of question objects.')
        if not 1 <= len(questions) <= 3:
            raise ValueError('ask_user requires 1-3 questions.')

        normalized_questions = []
        seen_ids = set()
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError('Each question must be an object.')

            question_id = str(question.get('id') or '').strip()[:64]
            if not question_id:
                raise ValueError('Each question requires a non-empty id.')
            if question_id in seen_ids:
                raise ValueError(f'Duplicate question id: {question_id}')
            seen_ids.add(question_id)

            options = question.get('options')
            if not isinstance(options, list) or not 2 <= len(options) <= 3:
                raise ValueError('Each question requires 2-3 options.')

            normalized_options = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError('Each option must be an object.')

                label = str(option.get('label') or '').strip()[:80]
                description = str(option.get('description') or '').strip()[:240]
                if not label or not description:
                    raise ValueError('Each option requires a label and description.')

                normalized_options.append(
                    {
                        'label': label,
                        'description': description,
                    }
                )

            question_text = str(question.get('question') or '').strip()[:500]
            if not question_text:
                raise ValueError('Each question requires question text.')

            normalized_questions.append(
                {
                    'id': question_id,
                    'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                    'question': question_text,
                    'options': normalized_options,
                    'allow_other': bool(question.get('allow_other', allow_other)),
                }
            )

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
            timeout_ms = 120_000

        if __event_call__ is None:
            return JSONCodec.dumps(
                {
                    'status': 'error',
                    'error': 'User input requires an active browser session with WebSocket connection.',
                },
                ensure_ascii=False,
            )

        output = await __event_call__(
            {
                'type': 'request:user_input',
                'data': {
                    'questions': normalized_questions,
                    'allow_other': allow_other,
                    'timeout_ms': timeout_ms,
                },
            }
        )

        if not isinstance(output, dict):
            return JSONCodec.dumps({'status': 'error', 'error': 'Invalid user input response.'}, ensure_ascii=False)
        if output.get('error'):
            return JSONCodec.dumps({'status': 'error', 'error': output.get('error')}, ensure_ascii=False)
        if output.get('status') == 'cancelled':
            return JSONCodec.dumps({'status': 'cancelled', 'answers': {}}, ensure_ascii=False)

        return JSONCodec.dumps(
            {
                'status': 'answered',
                'answers': output.get('answers', {}),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'ask_user error: {e}')
        return JSONCodec.dumps({'status': 'error', 'error': str(e)}, ensure_ascii=False)


# Code interpreter tools


async def execute_code(
    code: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __event_call__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute Python code in a sandboxed environment and return the output.
    Use this to perform calculations, data analysis, generate visualizations,
    or run any Python code that would help answer the user's question.

    :param code: The Python code to execute
    :return: JSON with stdout, stderr, and result from execution
    """
    from uuid import uuid4

    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        # Sanitize code (strips ANSI codes and markdown fences)
        code = sanitize_code(code)

        # Import blocked modules from config (same as middleware)
        from open_webui.config import CODE_INTERPRETER_BLOCKED_MODULES

        # Add import blocking code if there are blocked modules
        if CODE_INTERPRETER_BLOCKED_MODULES:
            import textwrap

            blocking_code = textwrap.dedent(
                f"""
                import builtins

                BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}

                _real_import = builtins.__import__
                def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                    if name.split('.')[0] in BLOCKED_MODULES:
                        importer_name = globals.get('__name__') if globals else None
                        if importer_name == '__main__':
                            raise ImportError(
                                f"Direct import of module {{name}} is restricted."
                            )
                    return _real_import(name, globals, locals, fromlist, level)

                builtins.__import__ = restricted_import
                """
            )
            code = blocking_code + '\n' + code

        engine = await Config.get('code_interpreter.engine', 'pyodide')
        if engine == 'pyodide':
            # Execute via frontend pyodide using bidirectional event call
            if __event_call__ is None:
                return JSONCodec.dumps(
                    {'error': 'Event call not available. WebSocket connection required for pyodide execution.'}
                )

            output = await __event_call__(
                {
                    'type': 'execute:python',
                    'data': {
                        'id': str(uuid4()),
                        'code': code,
                        'session_id': (__metadata__.get('session_id') if __metadata__ else None),
                        'files': (__metadata__.get('files', []) if __metadata__ else []),
                    },
                }
            )

            # Parse the output - pyodide returns dict with stdout, stderr, result
            if isinstance(output, dict):
                # Handle error responses from event_caller (e.g. session disconnected, timeout)
                if output.get('error') and not output.get('stdout') and not output.get('result'):
                    stderr = output['error']
                    stdout = ''
                    result = ''
                else:
                    stdout = output.get('stdout', '')
                    stderr = output.get('stderr', '')
                    result = output.get('result', '')
            else:
                stdout = ''
                stderr = ''
                result = str(output) if output else ''

        elif engine == 'jupyter':
            from open_webui.utils.code_interpreter import execute_code_jupyter

            jupyter_auth = await Config.get('code_interpreter.jupyter.auth')

            output = await execute_code_jupyter(
                await Config.get('code_interpreter.jupyter.url'),
                code,
                (await Config.get('code_interpreter.jupyter.auth_token') if jupyter_auth == 'token' else None),
                (await Config.get('code_interpreter.jupyter.auth_password') if jupyter_auth == 'password' else None),
                await Config.get('code_interpreter.jupyter.timeout'),
            )

            stdout = output.get('stdout', '')
            stderr = output.get('stderr', '')
            result = output.get('result', '')

        else:
            return JSONCodec.dumps({'error': f'Unknown code interpreter engine: {engine}'})

        # Handle image outputs (base64 encoded) - replace with uploaded URLs
        # Get actual user object for image upload (upload_image requires user.id attribute)
        if __user__ and __user__.get('id'):
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_url_from_base64

            user = await Users.get_user_by_id(__user__['id'])

            # Extract and upload images from stdout
            if stdout and isinstance(stdout, str):
                stdout_lines = stdout.split('\n')
                for idx, line in enumerate(stdout_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            stdout_lines[idx] = f'![Output Image]({image_url})'
                stdout = '\n'.join(stdout_lines)

            # Extract and upload images from result
            if result and isinstance(result, str):
                result_lines = result.split('\n')
                for idx, line in enumerate(result_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            result_lines[idx] = f'![Output Image]({image_url})'
                result = '\n'.join(result_lines)

        response = {
            'status': 'success',
            'stdout': stdout,
            'stderr': stderr,
            'result': result,
        }

        return JSONCodec.dumps(response, ensure_ascii=False)
    except Exception as e:
        log.exception(f'execute_code error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Memory tools


async def list_memory_paths(
    query: str = '',
    count: int = 100,
    type: str = 'all',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List saved memory paths to find existing memory groups before writing or moving memories.

    :param query: Optional query to filter memory paths or contents
    :param count: Maximum number of paths to return
    :param type: "user", "context", or "all"
    :return: JSON with memory paths, counts, children, and update times
    """
    try:
        user = UserModel(**__user__) if __user__ else None
        result = await _list_memory_paths(
            ListMemoryPathsForm(
                query=query or None,
                type=type if type in {'user', 'context', 'all'} else 'all',
                limit=count,
            ),
            user,
        )
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_memory_paths error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def read_memory_path(
    path: str,
    count: int = 50,
    type: str = 'all',
    include_children: bool = True,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read saved memories at a memory path, including nearby parent and child paths.

    :param path: Memory path to read
    :param count: Maximum number of memories to return
    :param type: "user", "context", or "all"
    :param include_children: Include memories under child paths
    :return: JSON with parent paths, child paths, and memories at the path
    """
    try:
        user = UserModel(**__user__) if __user__ else None
        result = await _read_memory_path(
            ReadMemoryPathForm(
                path=path,
                type=type if type in {'user', 'context', 'all'} else 'all',
                include_children=include_children,
                limit=count,
            ),
            user,
        )
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'read_memory_path error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_memories(
    query: str = '',
    count: int = 5,
    type: str = 'all',
    path: Optional[str] = None,
    memory_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search or browse saved memories by content, path, type, or memory ID.

    :param query: Optional query to search memory content and path
    :param count: Number of memories to return (default 5)
    :param type: "user", "context", or "all"
    :param path: Optional memory path to search around
    :param memory_id: Optional exact memory ID to read
    :return: JSON with matching memories and their dates
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memories = await _search_memories(
            SearchMemoriesForm(
                query=query or None,
                type=type if type in {'user', 'context', 'all'} else 'all',
                path=path,
                memory_id=memory_id,
                limit=count,
            ),
            user,
        )

        if not memories:
            return JSONCodec.dumps([])

        return JSONCodec.dumps(
            [
                {
                    'id': memory.id,
                    'type': memory.type,
                    'path': memory.path,
                    'content': memory.content,
                    'created_at': time.strftime('%Y-%m-%d', time.localtime(memory.created_at)),
                    'updated_at': time.strftime('%Y-%m-%d', time.localtime(memory.updated_at)),
                }
                for memory in memories
            ],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_memories error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def add_memory(
    content: str,
    type: str = 'user',
    path: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Save enduring information that can improve future chats.

    Save stable preferences, goals, projects, relationships, habits, and standing instructions.
    Do not save one-off activity, meals, routine daily events, temporary mood, or other short-lived details
    unless the user explicitly asks you to remember them.

    :param content: The memory content to store
    :param type: Use "user" for facts/preferences about the user, or "context" for other durable context
    :param path: Optional stable memory address for grouping related memories
    :return: Confirmation that the memory was stored
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memory = await _add_memory(
            __request__,
            AddMemoryForm(content=content, type=Memories.normalize_memory_type(type), path=path),
            user,
        )

        return JSONCodec.dumps(
            {'status': 'success', 'id': memory.id, 'type': memory.type, 'path': memory.path},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'add_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_memory(
    operations: list[dict],
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Apply a batch of memory changes after learning enduring information.

    Use type "user" for facts, preferences, or instructions about the user.
    Use type "context" for other durable context that may help future chats.
    Do not save one-off activity, meals, routine daily events, temporary mood, or other short-lived details
    unless the user explicitly asks you to remember them.
    Path is optional. Use it as a stable memory address to group related memories.
    Prefer an existing path from list_memory_paths when one fits.
    Leave path empty when no useful grouping is clear.

    Operation shapes:
    - {"action": "add", "content": "...", "type": "user"|"context", "path": "..."}
    - {"action": "replace", "id": "...", "content": "...", "type": "user"|"context", "path": "..."}
    - {"action": "move", "id": "...", "path": "..."}
    - {"action": "remove", "id": "..."}

    :param operations: Memory operations to apply in one request
    :return: JSON with operation results
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None
        operation_results = await _update_memories(
            __request__,
            UpdateMemoriesForm(operations=operations),
            user,
        )
        return JSONCodec.dumps(operation_results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'update_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def replace_memory_content(
    memory_id: str,
    content: str,
    type: Optional[str] = None,
    path: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing saved memory by its ID when its content needs correction.

    :param memory_id: The ID of the memory to update
    :param content: The new content for the memory
    :param type: Optional "user" or "context" type for the updated memory
    :param path: Optional stable memory address for grouping related memories
    :return: Confirmation that the memory was updated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memory = await update_memory_by_id(
            memory_id=memory_id,
            request=__request__,
            form_data=MemoryUpdateModel(
                content=content,
                type=Memories.normalize_memory_type(type) if type else None,
                path=path,
            ),
            user=user,
        )

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': memory.id,
                'type': memory.type,
                'path': memory.path,
                'content': memory.content,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'replace_memory_content error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_memory(
    memory_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a saved memory by its ID.

    :param memory_id: The ID of the memory to delete
    :return: Confirmation that the memory was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        result = await Memories.delete_memory_by_id_and_user_id(memory_id, user.id)

        if result:
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'user-memory-{user.id}', ids=[memory_id])
            return JSONCodec.dumps(
                {'status': 'success', 'message': f'Memory {memory_id} deleted'},
                ensure_ascii=False,
            )
        else:
            return JSONCodec.dumps({'error': 'Memory not found or access denied'})
    except Exception as e:
        log.exception(f'delete_memory error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_memories(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List all stored memories for the user, including IDs and timestamps.

    :return: JSON list of all memories with id, content, and dates
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        memories = await Memories.get_memories_by_user_id(user.id)

        if memories:
            memory_rows = [
                {
                    'id': m.id,
                    'type': m.type,
                    'path': m.path,
                    'content': m.content,
                    'created_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(m.created_at)),
                    'updated_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(m.updated_at)),
                }
                for m in memories
            ]
            return JSONCodec.dumps(memory_rows, ensure_ascii=False)
        else:
            return JSONCodec.dumps([])
    except Exception as e:
        log.exception(f'list_memories error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Notes tools


async def search_notes(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's saved notes by title and content.

    :param query: The search query to find matching notes
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include notes updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include notes updated before this Unix timestamp (seconds)
    :return: JSON with matching notes containing id, title, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Notes.search_notes(
            user_id=user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
                'permission': 'read',
            },
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        # Convert timestamps to nanoseconds for comparison
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        notes = []
        for note in result.items:
            # Apply date filters (updated_at is in nanoseconds)
            if start_ts and note.updated_at < start_ts:
                continue
            if end_ts and note.updated_at > end_ts:
                continue

            # Extract a snippet from the matching page.
            page = matching_page(note.data, query)
            content_snippet = ''
            if page['content'].get('md'):
                md_content = page['content']['md']
                content_lower = md_content.lower()

                # Find the first matching word to center the snippet around.
                search_words = query.lower().split()
                match_pos = -1
                match_len = len(query)
                for word in search_words:
                    found_pos = content_lower.find(word)
                    if found_pos != -1:
                        match_pos = found_pos
                        match_len = len(word)
                        break

                if match_pos != -1:
                    snippet_start = max(0, match_pos - 50)
                    snippet_end = min(len(md_content), match_pos + match_len + 100)
                    content_snippet = (
                        ('...' if snippet_start > 0 else '')
                        + md_content[snippet_start:snippet_end]
                        + ('...' if snippet_end < len(md_content) else '')
                    )
                else:
                    content_snippet = md_content[:150] + ('...' if len(md_content) > 150 else '')

            notes.append(
                {
                    'id': note.id,
                    'title': note.title,
                    'snippet': content_snippet,
                    'page_id': page['id'],
                    'page_title': page['title'],
                    'url': f'/notes/{note.id}?page={page["id"]}',
                    'updated_at': note.updated_at,
                }
            )

            if len(notes) >= count:
                break

        return JSONCodec.dumps(notes, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_notes error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_note(
    note_id: str,
    page_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full content of a note by its ID.

    :param note_id: The ID of the note to retrieve
    :param page_id: Page to read; omit to inspect the complete note and its page directory
    :return: JSON with the note's id, title, and full markdown content
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found'})

        # Check access permission
        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        from open_webui.models.access_grants import AccessGrants

        if (
            __user__.get('role') != 'admin'
            and note.user_id != user_id
            and not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='note',
                resource_id=note.id,
                permission='read',
                user_group_ids=set(user_group_ids),
            )
        ):
            return JSONCodec.dumps({'error': 'Access denied'})

        page = get_page(note.data, page_id) if page_id or len(note.data['pages']) == 1 else None
        content = page['content'].get('md', '') if page else note.data['content'].get('md', '')
        # Keep the version read by this tool run out of the model-facing interface.
        if not hasattr(__request__.state, 'note_page_revisions'):
            __request__.state.note_page_revisions = {}
        for read_page in [page] if page else note.data['pages']:
            __request__.state.note_page_revisions[(user_id, note.id, read_page['id'])] = read_page.get('revision', 0)

        return JSONCodec.dumps(
            {
                'id': note.id,
                'title': note.title,
                'content': content,
                'page_id': page['id'] if page else None,
                'page_title': page['title'] if page else None,
                'pages': [
                    {'id': p['id'], 'title': p['title'], 'parent_id': p.get('parent_id')} for p in note.data['pages']
                ],
                'updated_at': note.updated_at,
                'created_at': note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def write_note(
    title: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a new note with the given title and content.

    :param title: The title of the new note
    :param content: The markdown content for the note
    :return: JSON with success status and new note id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteForm

        user_id = __user__.get('id')

        form = NoteForm(
            title=title,
            data={'content': {'md': content}},
            access_grants=[],  # Private by default - only owner can access
        )

        editor = await Users.get_user_by_id(user_id)
        if not editor or not await has_access_to_note_files(None, get_content_files(form.data['content']), editor):
            return JSONCodec.dumps({'error': 'Cannot attach files you cannot access'})
        new_note = await Notes.insert_new_note(user_id, form)

        if not new_note:
            return JSONCodec.dumps({'error': 'Failed to create note'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': new_note.id,
                'title': new_note.title,
                'created_at': new_note.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'write_note error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def replace_note_content(
    note_id: str,
    content: Optional[str] = None,
    operations: Optional[list[dict]] = None,
    title: Optional[str] = None,
    page_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing note by replacing the whole markdown content, exact text, or character ranges.

    Read the note with view_note before editing. If it changes meanwhile, read it again before retrying.
    Prefer "replace_text" when only part of the note changes. Supply either find or start/end, not both.
    find is non-empty, literal, and case-sensitive.
    Text matches are non-overlapping. Multiple matches require a 0-indexed occurrence or replace_all=true;
    otherwise nothing is changed and match offsets and context are returned.
    A "replace" operation must be the only operation in the request.
    start and end are 0-indexed character offsets into the markdown content from view_note.
    end is exclusive.
    Offsets never shift as operations are applied.
    Ranges must not overlap.
    expected is optional. When set, the request is rejected if the range's current text does not match it.
    "replace_range" is a deprecated alias for the start/end form of "replace_text".

    :param note_id: The ID of the note to update
    :param content: The new markdown content for a whole-note update
    :param operations: Optional note operations:
    - {"action": "replace", "content": "..."}
    - {"action": "replace_text", "start": 0, "end": 10, "content": "...", "expected": "..."}
    - {"action": "replace_text", "find": "old text", "content": "new text", "occurrence": 0, "replace_all": false}
    :param title: Optional new title for the note
    :param page_id: Page to edit; required for notes with multiple pages. Use the active page_id supplied in the request unless the user selects another page
    :return: JSON with success status and updated note info
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.notes import NoteUpdateForm

        note = await Notes.get_note_by_id(note_id)

        if not note:
            return JSONCodec.dumps({'error': 'Note not found', 'code': 'not_found'})

        user_id = __user__.get('id')
        if __user__.get('role') != 'admin' and not await _has_write_access_to_note(note, user_id):
            return JSONCodec.dumps({'error': 'Write access denied', 'code': 'write_access_denied'})

        page = get_page(note.data, page_id)
        page_id = page['id']
        read_revisions = getattr(__request__.state, 'note_page_revisions', {})
        read_key = (user_id, note.id, page_id)
        if read_key not in read_revisions:
            return JSONCodec.dumps({'error': 'Read this page with view_note before editing', 'code': 'read_required'})
        read_revision = read_revisions[read_key]
        if read_revision != page.get('revision', 0):
            raise NotePageConflict('Page changed since you read it. Use view_note again before editing')
        current_content = page['content'].get('md') or ''
        applied_operation_count = 0
        range_operations = []
        if operations is not None:
            if not isinstance(operations, list) or len(operations) == 0:
                return JSONCodec.dumps({'error': 'operations must be a non-empty list', 'code': 'invalid_operations'})

            for idx, operation in enumerate(operations):
                if not isinstance(operation, dict):
                    return JSONCodec.dumps(
                        {'error': 'each operation must be an object', 'code': 'invalid_operation', 'index': idx}
                    )

                action = operation.get('action')
                replacement = operation.get('content')

                if action == 'replace':
                    if len(operations) != 1:
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation must be the only operation',
                                'code': 'invalid_operations',
                                'index': idx,
                            }
                        )
                    if not isinstance(replacement, str):
                        return JSONCodec.dumps(
                            {
                                'error': 'replace operation content must be a string',
                                'code': 'invalid_content',
                                'index': idx,
                            }
                        )
                    content = replacement
                    applied_operation_count = 1
                    break

                if action not in ('replace_range', 'replace_text'):
                    return JSONCodec.dumps(
                        {'error': 'unknown operation action', 'code': 'invalid_action', 'index': idx, 'action': action}
                    )

                if not isinstance(replacement, str):
                    return JSONCodec.dumps(
                        {'error': 'operation content must be a string', 'code': 'invalid_content', 'index': idx}
                    )
                if action == 'replace_text' and 'find' in operation:
                    if 'start' in operation or 'end' in operation:
                        return JSONCodec.dumps(
                            {
                                'error': 'use either find or start/end, not both',
                                'code': 'invalid_operation',
                                'index': idx,
                            }
                        )
                    find = operation.get('find')
                    occurrence = operation.get('occurrence')
                    replace_all = operation.get('replace_all', False)
                    if not isinstance(find, str) or not find:
                        return JSONCodec.dumps(
                            {'error': 'find must be a non-empty string', 'code': 'invalid_find', 'index': idx}
                        )
                    if (
                        type(replace_all) is not bool
                        or (occurrence is not None and (type(occurrence) is not int or occurrence < 0))
                        or (replace_all and occurrence is not None)
                    ):
                        return JSONCodec.dumps(
                            {
                                'error': 'use a non-negative occurrence or replace_all boolean',
                                'code': 'invalid_occurrence',
                                'index': idx,
                            }
                        )

                    matches = []
                    start = current_content.find(find)
                    while start != -1:
                        end = start + len(find)
                        matches.append({'start': start, 'end': end})
                        start = current_content.find(find, end)

                    if not matches:
                        return JSONCodec.dumps({'error': 'Text not found', 'code': 'text_not_found', 'index': idx})
                    if occurrence is not None and occurrence >= len(matches):
                        return JSONCodec.dumps(
                            {'error': 'occurrence is out of bounds', 'code': 'invalid_occurrence', 'index': idx}
                        )
                    if len(matches) > 1 and occurrence is None and not replace_all:
                        return JSONCodec.dumps(
                            {
                                'error': 'Multiple matches; specify occurrence or replace_all',
                                'code': 'ambiguous_match',
                                'index': idx,
                                'matches': [
                                    {
                                        **match,
                                        'context': current_content[max(0, match['start'] - 50) : match['end'] + 50],
                                    }
                                    for match in matches
                                ],
                            },
                            ensure_ascii=False,
                        )
                    if occurrence is not None:
                        matches = [matches[occurrence]]
                    range_operations.extend({**match, 'content': replacement} for match in matches)
                    continue

                start = operation.get('start')
                end = operation.get('end')
                expected = operation.get('expected')
                if not isinstance(start, int) or not isinstance(end, int):
                    return JSONCodec.dumps(
                        {'error': 'operation start and end must be integers', 'code': 'invalid_range', 'index': idx}
                    )
                if start < 0 or end < start or end > len(current_content):
                    return JSONCodec.dumps(
                        {'error': 'operation range is out of bounds', 'code': 'range_out_of_bounds', 'index': idx}
                    )
                if expected is not None and current_content[start:end] != expected:
                    return JSONCodec.dumps(
                        {
                            'error': 'operation expected text does not match current content',
                            'code': 'expected_mismatch',
                            'index': idx,
                        }
                    )

                range_operations.append({'start': start, 'end': end, 'content': replacement})

            range_operations.sort(key=lambda operation: operation['start'])
            previous_end = 0
            for idx, operation in enumerate(range_operations):
                if operation['start'] < previous_end:
                    return JSONCodec.dumps(
                        {'error': 'operation ranges must not overlap', 'code': 'overlapping_operations', 'index': idx}
                    )
                previous_end = operation['end']

            if range_operations:
                content = current_content
                for operation in reversed(range_operations):
                    content = content[: operation['start']] + operation['content'] + content[operation['end'] :]
                applied_operation_count = len(range_operations)
        elif content is None:
            return JSONCodec.dumps({'error': 'content or operations is required', 'code': 'content_required'})

        update_data = {
            'page_id': page_id,
            'page_revision': read_revision,
            'data': {
                'content': {
                    'json': None,
                    'html': '',
                    'md': content,
                },
            },
        }
        if title:
            update_data['title'] = title

        editor = await Users.get_user_by_id(__user__['id'])
        if not editor or not await has_access_to_note_files(
            note, get_content_files(update_data['data']['content']), editor
        ):
            return JSONCodec.dumps({'error': 'Cannot attach files you cannot access'})
        form = NoteUpdateForm(**update_data)
        updated_note = await Notes.update_note_by_id(note_id, form)

        if not updated_note:
            return JSONCodec.dumps({'error': 'Failed to update note', 'code': 'update_failed'})

        read_revisions[read_key] = get_page(updated_note.data, page_id).get('revision', 0)
        await close_changed_note_documents(note, updated_note)
        await _emit_note_updated(__request__, __user__, updated_note)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated_note.id,
                'page_id': page_id,
                'title': updated_note.title,
                'updated_at': updated_note.updated_at,
                'applied_operation_count': applied_operation_count,
                'applied_ranges': [{'start': op['start'], 'end': op['end']} for op in range_operations],
            },
            ensure_ascii=False,
        )
    except NotePageConflict as e:
        return JSONCodec.dumps({'error': str(e), 'code': 'page_conflict'})
    except Exception as e:
        log.exception(f'replace_note_content error: {e}')
        return JSONCodec.dumps({'error': str(e), 'code': 'unexpected_error'})


# Chats tools


async def search_chats(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search the user's previous chat conversations by title and message content,
    excluding the current chat. Helpful for finding details from earlier
    conversations when they are not already visible in the current context.
    Exact phrase matches are preferred, and descriptive keyword queries are
    supported.

    :param query: Exact phrase or descriptive keyword query to find matching previous chats
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include chats updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include chats updated before this Unix timestamp (seconds)
    :return: JSON with matching chats containing id, title, updated_at, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chats = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=query,
            include_archived=False,
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        matched = []
        for chat in chats:
            # Skip the current chat to avoid showing it in search results
            if __chat_id__ and chat.id == __chat_id__:
                continue

            # Apply date filters (updated_at is in seconds)
            if start_timestamp and chat.updated_at < start_timestamp:
                continue
            if end_timestamp and chat.updated_at > end_timestamp:
                continue

            matched.append(chat)
            if len(matched) >= count:
                break

        snippets = await Chats.get_chat_search_snippets([chat.id for chat in matched], user_id, query)
        lower_query = chat_search_content_query(query)
        needles = list(dict.fromkeys([lower_query, *chat_search_terms(lower_query)])) if lower_query else []
        results = []
        for chat in matched:
            snippet = snippets.get(chat.id) or ''
            title = chat.title or ''
            if not snippet and any(needle in title.lower() for needle in needles):
                snippet = f'Title match: {title}'

            results.append(
                {
                    'id': chat.id,
                    'title': chat.title,
                    'snippet': snippet,
                    'updated_at': chat.updated_at,
                }
            )

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_chats error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_chat(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full conversation history of a chat by its ID after a relevant
    previous chat has been identified.

    :param chat_id: The ID of the chat to retrieve
    :return: JSON with the chat's id, title, and messages
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

        if not chat:
            return JSONCodec.dumps({'error': 'Chat not found or access denied'})

        # Extract messages from history
        messages = []
        history = chat.chat.get('history', {})
        msg_dict = history.get('messages', {})

        # Follow parent links from currentId; conversations have family trees too.
        current_id = history.get('currentId')
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            msg = msg_dict.get(current_id)
            if msg:
                messages.append(
                    {
                        'role': msg.get('role', ''),
                        'content': msg.get('content', ''),
                    }
                )
            current_id = msg.get('parentId') if msg else None

        truncation = {}
        max_chars = await Config.get('chats.view_max_chars')
        if max_chars and sum(len(str(message['content'] or '')) for message in messages) > max_chars:
            remaining_chars = max_chars
            kept_messages = []
            for message in messages:
                if remaining_chars <= 0:
                    break
                content = str(message['content'] or '')[:remaining_chars]
                kept_messages.append({**message, 'content': content})
                remaining_chars -= len(content)
            truncation = {
                'truncated': True,
                'total_messages': len(messages),
                'returned_messages': len(kept_messages),
            }
            messages = kept_messages

        # Reverse to get chronological order
        messages.reverse()

        return JSONCodec.dumps(
            {
                'id': chat.id,
                'title': chat.title,
                'messages': messages,
                'updated_at': chat.updated_at,
                'created_at': chat.created_at,
                **truncation,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_chat error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Sub-agent tool


async def delegate_task(
    task: str,
    context: str = '',
    file_ids: list[str] | None = None,
    background: bool = False,
    model_id: str | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Delegate focused work to a parallel sub-agent using the current tools.

    :param task: The specific task for the sub-agent to complete
    :param context: Relevant context, decisions, or file paths for the task
    :param file_ids: Attached file IDs the sub-agent needs. Use this for images or files;
        do not put file IDs only in context.
    :param background: Return immediately and continue this chat when the sub-agent finishes
    :param model_id: ID of an available sub-agent model. Omit to use the current model
    :return: Foreground result text, or a JSON dispatch handle for background work
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: sub-agents cannot delegate recursively.'

    from open_webui.utils.subagents import delegate

    return await delegate(
        task,
        context,
        background,
        file_ids=file_ids,
        model_id=model_id,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


async def timer(
    prompt: str,
    at: str,
    cancel_on: list[Literal['chat.read', 'chat.user_message']] | None = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Set a one-shot timer for this chat.

    :param prompt: The prompt to send back into this chat when the timer fires
    :param at: Relative time like 10s, 5m, 1h, 2d, or a timezone-aware RFC 3339 timestamp
    :param cancel_on: Optional events that cancel the timer before it fires
    :return: JSON status with the scheduled time, or an error string
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: timers cannot be set from internal chats.'

    from open_webui.utils.timers import create_timer

    return await create_timer(
        prompt=prompt,
        at=at,
        cancel_on=cancel_on,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


# Channels tools


def _current_channel_id(chat_id) -> Optional[str]:
    """Channel the model was @mentioned in, or None outside a channel.

    The 'channel:' chat ID is only accepted by /api/chat/completions after its membership
    and write-access gate, so the channel tools can trust it to scope their results.
    """
    if isinstance(chat_id, str) and chat_id.startswith(CHANNEL_CHAT_ID_PREFIX):
        return chat_id.removeprefix(CHANNEL_CHAT_ID_PREFIX) or None
    return None


async def search_channels(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search channels by name and description to find accessible team spaces.
    When called from inside a channel, only that channel is returned.

    :param query: The search query to find matching channels
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with matching channels containing id, name, description, and type
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        current_channel_id = _current_channel_id(__chat_id__)
        if current_channel_id:
            # Inside a channel the model must stay in that channel.
            channel = await Channels.get_channel_by_id(current_channel_id)
            if not channel:
                return JSONCodec.dumps([])
            return JSONCodec.dumps(
                [
                    {
                        'id': channel.id,
                        'name': channel.name,
                        'description': channel.description or '',
                        'type': channel.type or 'public',
                    }
                ],
                ensure_ascii=False,
            )

        # Get all channels the user has access to
        all_channels = await Channels.get_channels_by_user_id(user_id)

        # Filter by query
        lower_query = query.lower()
        matching_channels = []

        for channel in all_channels:
            name_match = lower_query in channel.name.lower() if channel.name else False
            desc_match = lower_query in (channel.description or '').lower()

            if name_match or desc_match:
                matching_channels.append(
                    {
                        'id': channel.id,
                        'name': channel.name,
                        'description': channel.description or '',
                        'type': channel.type or 'public',
                    }
                )

            if len(matching_channels) >= count:
                break

        return JSONCodec.dumps(matching_channels, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channels error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_channel_messages(
    query: str,
    count: int = 10,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search messages in channels the user is a member of, including thread replies.
    Helpful for finding prior team/channel discussion.
    When called from inside a channel, only messages of that channel are searched.

    :param query: The search query to find matching messages
    :param count: Maximum number of results to return (default: 10)
    :param start_timestamp: Only include messages created after this Unix timestamp (seconds)
    :param end_timestamp: Only include messages created before this Unix timestamp (seconds)
    :return: JSON with matching messages containing channel info, message content, and thread context
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        current_channel_id = _current_channel_id(__chat_id__)
        if current_channel_id:
            # Inside a channel, never mix in messages from the user's other channels.
            channel = await Channels.get_channel_by_id(current_channel_id)
            user_channels = [channel] if channel else []
        else:
            # Get all channels the user has access to
            user_channels = await Channels.get_channels_by_user_id(user_id)
        channel_ids = [c.id for c in user_channels]
        channel_map = {c.id: c for c in user_channels}

        if not channel_ids:
            return JSONCodec.dumps([])

        # Convert timestamps to nanoseconds (Message.created_at is in nanoseconds)
        start_ts = start_timestamp * 1_000_000_000 if start_timestamp else None
        end_ts = end_timestamp * 1_000_000_000 if end_timestamp else None

        # Search messages using the model method
        matching_messages = await Messages.search_messages_by_channel_ids(
            channel_ids=channel_ids,
            query=query,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            limit=count,
        )

        results = []
        for msg in matching_messages:
            channel = channel_map.get(msg.channel_id)

            # Extract snippet around the match
            content = msg.content or ''
            lower_query = query.lower()
            idx = content.lower().find(lower_query)
            if idx != -1:
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 100)
                snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
            else:
                snippet = content[:150] + ('...' if len(content) > 150 else '')

            results.append(
                {
                    'channel_id': msg.channel_id,
                    'channel_name': channel.name if channel else 'Unknown',
                    'message_id': msg.id,
                    'content_snippet': snippet,
                    'is_thread_reply': msg.parent_id is not None,
                    'parent_id': msg.parent_id,
                    'created_at': msg.created_at,
                }
            )

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_channel_messages error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_channel_message(
    message_id: str,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Get the full content of a channel message by its ID, including thread replies.
    When called from inside a channel, only messages of that channel can be viewed.

    :param message_id: The ID of the message to retrieve
    :return: JSON with the message content, channel info, and thread replies if any
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        message = await Messages.get_message_by_id(message_id)

        if not message:
            return JSONCodec.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(message.channel_id)
        if not channel:
            return JSONCodec.dumps({'error': 'Channel not found'})

        current_channel_id = _current_channel_id(__chat_id__)
        if current_channel_id:
            if message.channel_id != current_channel_id:
                return JSONCodec.dumps({'error': 'Message is outside the current channel'})
        else:
            # Check if user has access to the channel
            user_channels = await Channels.get_channels_by_user_id(user_id)
            channel_ids = [c.id for c in user_channels]

            if message.channel_id not in channel_ids:
                return JSONCodec.dumps({'error': 'Access denied'})

        # Build response with thread information
        result = {
            'id': message.id,
            'channel_id': message.channel_id,
            'channel_name': channel.name,
            'content': message.content,
            'user_id': message.user_id,
            'is_thread_reply': message.parent_id is not None,
            'parent_id': message.parent_id,
            'reply_count': message.reply_count,
            'created_at': message.created_at,
            'updated_at': message.updated_at,
        }

        # Include user info if available
        if message.user:
            result['user_name'] = message.user.name

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_channel_message error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_channel_thread(
    parent_message_id: str,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Get all messages in a channel thread, including the parent message and all replies.
    When called from inside a channel, only threads of that channel can be viewed.

    :param parent_message_id: The ID of the parent message that started the thread
    :return: JSON with the parent message and all thread replies in chronological order
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        # Get the parent message
        parent_message = await Messages.get_message_by_id(parent_message_id)

        if not parent_message:
            return JSONCodec.dumps({'error': 'Message not found'})

        # Verify user has access to the channel
        channel = await Channels.get_channel_by_id(parent_message.channel_id)
        if not channel:
            return JSONCodec.dumps({'error': 'Channel not found'})

        current_channel_id = _current_channel_id(__chat_id__)
        if current_channel_id:
            if parent_message.channel_id != current_channel_id:
                return JSONCodec.dumps({'error': 'Thread is outside the current channel'})
        else:
            user_channels = await Channels.get_channels_by_user_id(user_id)
            channel_ids = [c.id for c in user_channels]

            if parent_message.channel_id not in channel_ids:
                return JSONCodec.dumps({'error': 'Access denied'})

        # Get all thread replies
        thread_replies = await Messages.get_thread_replies_by_message_id(parent_message_id)

        # Build the response
        messages = []

        # Parent message first; the replies need something to reply to.
        messages.append(
            {
                'id': parent_message.id,
                'content': parent_message.content,
                'user_id': parent_message.user_id,
                'user_name': parent_message.user.name if parent_message.user else None,
                'is_parent': True,
                'created_at': parent_message.created_at,
            }
        )

        # Reverse the replies into chronological order; spoilers go at the end.
        for reply in reversed(thread_replies):
            messages.append(
                {
                    'id': reply.id,
                    'content': reply.content,
                    'user_id': reply.user_id,
                    'user_name': reply.user.name if reply.user else None,
                    'is_parent': False,
                    'reply_to_id': reply.reply_to_id,
                    'created_at': reply.created_at,
                }
            )

        return JSONCodec.dumps(
            {
                'channel_id': parent_message.channel_id,
                'channel_name': channel.name,
                'thread_id': parent_message_id,
                'message_count': len(messages),
                'messages': messages,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_channel_thread error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Knowledge base tools


async def list_knowledge_bases(
    count: int = 10,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's accessible knowledge bases so a relevant internal source
    can be chosen.

    :param count: Maximum number of KBs to return (default: 10)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with KBs containing id, name, description, and file_count
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Knowledges.search_knowledge_bases(
            user_id,
            filter={
                'query': '',
                'user_id': user_id,
                'group_ids': user_group_ids,
            },
            skip=skip,
            limit=count,
        )

        knowledge_bases = []
        for knowledge_base in result.items:
            files = await Knowledges.get_files_by_id(knowledge_base.id)
            file_count = len(files) if files else 0

            knowledge_bases.append(
                {
                    'id': knowledge_base.id,
                    'name': knowledge_base.name,
                    'description': knowledge_base.description or '',
                    'file_count': file_count,
                    'updated_at': knowledge_base.updated_at,
                }
            )

        return JSONCodec.dumps(knowledge_bases, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_knowledge_bases(
    query: str,
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's accessible knowledge bases by name and description to find
    a relevant internal source.

    :param query: The search query to find matching knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching KBs containing id, name, description, and file_count
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        result = await Knowledges.search_knowledge_bases(
            user_id,
            filter={
                'query': query,
                'user_id': user_id,
                'group_ids': user_group_ids,
            },
            skip=skip,
            limit=count,
        )

        knowledge_bases = []
        for knowledge_base in result.items:
            files = await Knowledges.get_files_by_id(knowledge_base.id)
            file_count = len(files) if files else 0

            knowledge_bases.append(
                {
                    'id': knowledge_base.id,
                    'name': knowledge_base.name,
                    'description': knowledge_base.description or '',
                    'file_count': file_count,
                    'updated_at': knowledge_base.updated_at,
                }
            )

        return JSONCodec.dumps(knowledge_bases, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_knowledge_files(
    query: str,
    knowledge_id: Optional[str] = None,
    count: int = 5,
    skip: int = 0,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    Search files by filename across knowledge bases the user has access to.
    When the model has attached knowledge, searches only within attached KBs and files.
    Helpful when looking for a specific document or file name.

    :param query: The search query to find matching files by filename
    :param knowledge_id: Optional KB id to limit search to a specific knowledge base
    :param count: Maximum number of results to return (default: 5)
    :param skip: Number of results to skip for pagination (default: 0)
    :return: JSON with matching files containing id, filename, and updated_at
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        # When model has attached knowledge, scope to attached KBs/files only
        if __model_knowledge__:
            attached_kb_ids = set()
            attached_file_ids = set()

            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')
                if item_type == 'collection':
                    attached_kb_ids.add(item_id)
                elif item_type == 'file':
                    attached_file_ids.add(item_id)

            # If knowledge_id specified, verify it's in the attached set
            if knowledge_id:
                if knowledge_id not in attached_kb_ids:
                    return JSONCodec.dumps({'error': f'Knowledge base {knowledge_id} is not attached to this model'})
                attached_kb_ids = {knowledge_id}

            all_files = []

            # Search within attached KBs
            for kb_id in attached_kb_ids:
                knowledge = await Knowledges.get_knowledge_by_id(kb_id)
                if not knowledge:
                    continue

                if not (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    continue

                result = await Knowledges.search_files_by_id(
                    knowledge_id=kb_id,
                    user_id=user_id,
                    filter={'query': query},
                    skip=0,
                    limit=count + skip,
                )

                for file in result.items:
                    all_files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'knowledge_id': knowledge.id,
                            'knowledge_name': knowledge.name,
                            'updated_at': file.updated_at,
                        }
                    )

            # Search within directly attached files (filename match)
            if not knowledge_id and attached_file_ids:
                query_lower = query.lower() if query else ''
                for file_id in attached_file_ids:
                    file = await Files.get_file_by_id(file_id)
                    if file and (not query_lower or query_lower in file.filename.lower()):
                        all_files.append(
                            {
                                'id': file.id,
                                'filename': file.filename,
                                'updated_at': file.updated_at,
                            }
                        )

            # Apply pagination across combined results
            all_files = all_files[skip : skip + count]
            return JSONCodec.dumps(all_files, ensure_ascii=False)

        # No attached knowledge - search all accessible KBs
        if knowledge_id:
            # search_files_by_id does not enforce knowledge_id ownership; mirror the attached-KB check above.
            knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
            if not knowledge or not (
                user_role == 'admin'
                or knowledge.user_id == user_id
                or await AccessGrants.has_access(
                    user_id=user_id,
                    resource_type='knowledge',
                    resource_id=knowledge.id,
                    permission='read',
                    user_group_ids=set(user_group_ids),
                )
            ):
                return JSONCodec.dumps({'error': f'Access denied to knowledge base {knowledge_id}'})

            result = await Knowledges.search_files_by_id(
                knowledge_id=knowledge_id,
                user_id=user_id,
                filter={'query': query},
                skip=skip,
                limit=count,
            )
        else:
            result = await Knowledges.search_knowledge_files(
                filter={
                    'query': query,
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=skip,
                limit=count,
            )

        files = []
        for file in result.items:
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'updated_at': file.updated_at,
            }
            if hasattr(file, 'collection') and file.collection:
                file_info['knowledge_id'] = file.collection.get('id', '')
                file_info['knowledge_name'] = file.collection.get('name', '')
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def _get_accessible_chat_files(
    files: Optional[list[dict]],
    user: dict,
    file_id: Optional[str] = None,
) -> list[tuple[dict, object]]:
    from open_webui.models.files import Files

    accessible = []
    seen = set()

    for item in files or []:
        if not isinstance(item, dict) or item.get('type', 'file') != 'file':
            continue
        fid = item.get('id') or item.get('url') or ''
        if (
            not isinstance(fid, str)
            or not fid
            or fid in seen
            or fid.startswith(('http://', 'https://', 'data:'))
            or (file_id and fid != file_id)
        ):
            continue
        normalized = {**item, 'id': fid, 'type': 'file'}
        if 'name' not in normalized and item.get('filename'):
            normalized['name'] = item.get('filename')
        seen.add(fid)

        file = await Files.get_file_by_id(fid)
        if file and await _has_read_access_to_file(file, user):
            accessible.append((normalized, file))

    return accessible


def _grep_file_models(
    files_to_search: list,
    pattern: str,
    case_insensitive: bool = False,
    count_only: bool = False,
) -> str:
    from open_webui.tools.knowledge_fs import build_matcher

    matches, err = build_matcher(pattern, case_insensitive)
    if err:
        return JSONCodec.dumps({'error': err})

    results = []
    total_matches = 0
    counts = []

    for file in files_to_search:
        content = ''
        if file.data:
            content = file.data.get('content', '')
        if not content:
            continue

        lines = content.split('\n')
        file_matches = 0

        for i, line in enumerate(lines, 1):
            if matches(line):
                file_matches += 1
                total_matches += 1
                if not count_only and len(results) < KNOWLEDGE_GREP_MAX_MATCHES:
                    results.append(f'{file.id}  {file.filename}:{i}: {line}')

        if file_matches > 0 and count_only:
            counts.append(f'{file.id}  {file.filename}: {file_matches}')

    if count_only:
        if not counts:
            return f'No matches for "{pattern}"'
        return '\n'.join(counts) + f'\n[{total_matches} total matches]'

    if not results:
        return f'No matches for "{pattern}"'

    output = '\n'.join(results)
    if total_matches > KNOWLEDGE_GREP_MAX_MATCHES:
        output += f'\n[{KNOWLEDGE_GREP_MAX_MATCHES} of {total_matches} matches shown — use file_id to narrow]'
    return output


async def list_chat_files(
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    List files attached to the current chat.

    :return: JSON with attached chat files containing id, filename, content type, size, and updated time when available
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        files = []
        for item, file in await _get_accessible_chat_files(__files__, __user__):
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'name': item.get('name') or file.filename,
                'type': item.get('type', 'file'),
                'updated_at': file.updated_at,
            }
            content_type = item.get('content_type') or (file.meta or {}).get('content_type')
            size = item.get('size') or (file.meta or {}).get('size')
            if content_type:
                file_info['content_type'] = content_type
            if size:
                file_info['size'] = size
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def grep_chat_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    Search exact text across files attached to the current chat.
    Pass file_id from the attached_files block to search one file.
    Auto-detected regex uses RE2 syntax; no lookarounds/backreferences, and shorthand classes are ASCII-only.

    :param pattern: The text pattern to search for
    :param file_id: Optional attached file ID to search within a single file
    :param case_insensitive: If true, ignore case when matching
    :param count_only: If true, return only match counts per file
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None

    try:
        attached_ids = set()
        for item in __files__ or []:
            if not isinstance(item, dict) or item.get('type', 'file') != 'file':
                continue
            fid = item.get('id') or item.get('url')
            if isinstance(fid, str) and fid and not fid.startswith(('http://', 'https://', 'data:')):
                attached_ids.add(fid)

        if not attached_ids:
            return JSONCodec.dumps({'error': 'No files are attached to this chat'})
        if file_id and file_id not in attached_ids:
            return JSONCodec.dumps({'error': 'File not found'})

        files_to_search = [file for _, file in await _get_accessible_chat_files(__files__, __user__, file_id)]
        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(_grep_file_models, files_to_search, pattern, case_insensitive, count_only)
    except Exception as e:
        log.exception(f'grep_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_chat_files(
    query: str,
    file_id: Optional[str] = None,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
    __metadata__: dict = None,
) -> str:
    """
    Search files attached to the current chat using semantic/vector search and return the matching passages.
    Use it to find specific facts; to read a whole file use view_file, to summarize a long file use
    summarize_chat_file.
    Pass file_id from the attached_files block to search one file, or omit it to search all attached chat files.

    :param query: The search query to find semantically relevant content
    :param file_id: Optional attached file ID to search within a single file
    :param count: Maximum number of results to return, capped by the server RAG top k
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None
    if isinstance(count, str):
        if count.lower() in ('none', 'null', ''):
            count = None
        else:
            try:
                count = int(count)
            except ValueError:
                count = None

    try:
        from open_webui.retrieval.utils import get_sources_from_items

        attached_ids = set()
        for item in __files__ or []:
            if not isinstance(item, dict) or item.get('type', 'file') != 'file':
                continue
            fid = item.get('id') or item.get('url')
            if isinstance(fid, str) and fid and not fid.startswith(('http://', 'https://', 'data:')):
                attached_ids.add(fid)

        if not attached_ids:
            return JSONCodec.dumps({'error': 'No files are attached to this chat'})
        if exhausted := await _read_budget_exhausted(__metadata__):
            return exhausted
        if file_id and file_id not in attached_ids:
            return JSONCodec.dumps({'error': 'File not found'})

        accessible = await _get_accessible_chat_files(__files__, __user__, file_id)
        if not accessible:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        file_items = [{**item} for item, _ in accessible]
        rag_config = await Config.get_many(
            'rag.top_k',
            'rag.top_k_reranker',
            'rag.relevance_threshold',
            'rag.hybrid_bm25_weight',
            'rag.enable_hybrid_search',
            'rag.full_context',
        )
        top_k = rag_config.get('rag.top_k') or 5
        count = top_k if count is None else max(1, min(count, top_k))
        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        # A search returns matching passages; whole files are read with view_file or summarize_chat_file.
        # Global full-context mode is for automatic RAG injection, so it only applies here without embeddings.
        full_context = all(item.get('context') == 'full' for item in file_items) or (
            not embedding_function and rag_config.get('rag.full_context')
        )
        if not embedding_function and not full_context:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})

        user_model = UserModel(**__user__)
        sources = await get_sources_from_items(
            request=__request__,
            items=file_items,
            queries=[query],
            embedding_function=(
                lambda queries, prefix: (
                    embedding_function(queries, prefix=prefix, user=user_model) if embedding_function else None
                )
            ),
            k=count,
            reranking_function=(
                (lambda q, docs: __request__.app.state.RERANKING_FUNCTION(q, docs, user=user_model))
                if getattr(__request__.app.state, 'RERANKING_FUNCTION', None)
                else None
            ),
            k_reranker=rag_config.get('rag.top_k_reranker'),
            r=rag_config.get('rag.relevance_threshold'),
            hybrid_bm25_weight=rag_config.get('rag.hybrid_bm25_weight'),
            hybrid_search=rag_config.get('rag.enable_hybrid_search'),
            full_context=full_context,
            user=user_model,
            full_context_char_threshold=0,
        )

        chunks = []
        for source in sources or []:
            documents = source.get('document') or []
            metadatas = source.get('metadata') or []
            distances = source.get('distances') or []
            source_info = source.get('source') or {}

            for idx, doc in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
                chunk = {
                    **filter_source_metadata(metadata),
                    'content': doc,
                    'source': metadata.get('source', metadata.get('name', source_info.get('name', 'Unknown'))),
                    'file_id': metadata.get('file_id', source_info.get('id', '')),
                }
                if idx < len(distances):
                    chunk['distance'] = distances[idx]
                chunks.append(chunk)

        # Keep the result within budget so repeated searches cannot flood the context.
        from open_webui.file_pipeline.settings import file_settings

        result_budget = (await file_settings())['files.query_max_chars']
        selected, used = [], 0
        for chunk in chunks[:count]:
            content = chunk.get('content') or ''
            remaining = result_budget - used
            if remaining <= 0:
                break
            if len(content) > remaining:
                chunk = {
                    **chunk,
                    'content': content[:remaining],
                    'truncated': True,
                    'hint': 'Passage cut to fit the result budget; read the rest with view_file.',
                }
            selected.append(chunk)
            used += len(chunk['content'])
        _charge_read(__metadata__, ''.join(chunk['content'] for chunk in selected))
        return JSONCodec.dumps(selected, ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def grep_knowledge_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    Search for exact text across knowledge files. Returns matching lines with line numbers.
    Unlike query_knowledge_files (semantic/vector search), this performs exact string matching.
    Automatically detects regex patterns (e.g. "error|warn", "version \\d+").
    Regex uses RE2 syntax; no lookarounds/backreferences, and shorthand character classes are ASCII-only.
    Helpful for literal strings, identifiers, error messages, or regex-style searches.

    :param pattern: The text pattern to search for (regex auto-detected)
    :param file_id: Optional file ID to search within a single file only
    :param case_insensitive: If true, ignore case when matching (default: false)
    :param count_only: If true, return only match counts per file (default: false)
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    try:
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        # Collect files to search
        files_to_search = []

        if file_id:
            # Single file mode — verify access
            file = await Files.get_file_by_id(file_id)
            if file:
                if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
                    return JSONCodec.dumps({'error': 'File not found'})
                files_to_search.append(file)
        elif __model_knowledge__:
            # Scoped to model's attached knowledge
            from open_webui.models.access_grants import AccessGrants

            seen_ids = set()
            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')
                if item_type == 'file' and item_id not in seen_ids:
                    file = await Files.get_file_by_id(item_id)
                    if file:
                        files_to_search.append(file)
                        seen_ids.add(item_id)
                elif item_type == 'collection':
                    knowledge = await Knowledges.get_knowledge_by_id(item_id)
                    if not knowledge:
                        continue
                    # Verify user can access this KB
                    if not (
                        user_role == 'admin'
                        or knowledge.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='read',
                            user_group_ids=set(user_group_ids),
                        )
                    ):
                        continue
                    kb_files = await Knowledges.get_files_by_id(item_id)
                    if kb_files:
                        for f in kb_files:
                            if f.id not in seen_ids:
                                files_to_search.append(f)
                                seen_ids.add(f.id)
        else:
            # All accessible knowledge bases — use the same search pattern as list_knowledge_bases
            result = await Knowledges.search_knowledge_bases(
                user_id,
                filter={
                    'query': '',
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=0,
                limit=200,
            )
            seen_ids = set()
            for kb in result.items:
                file_ids = []
                # Get files attached to this KB
                files_from_kb = await Knowledges.get_files_by_id(kb.id)
                if files_from_kb:
                    file_ids = [f.id for f in files_from_kb]
                for fid in file_ids:
                    if fid not in seen_ids:
                        file = await Files.get_file_by_id(fid)
                        if file:
                            files_to_search.append(file)
                            seen_ids.add(fid)

        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return await asyncio.to_thread(_grep_file_models, files_to_search, pattern, case_insensitive, count_only)

    except Exception as e:
        log.exception(f'grep_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


def _numbered_lines_within_cap(all_lines: list[str], start: int, end: int) -> tuple[str, int]:
    """Number lines start..end, stopping at the last whole line that fits VIEW_FILE_MAX_CHARS."""
    numbered_lines = []
    used_chars = 0
    for line_index in range(start, end):
        numbered_line = f'{line_index + 1}: {all_lines[line_index]}'
        if used_chars + len(numbered_line) > VIEW_FILE_MAX_CHARS:
            if numbered_lines:
                return '\n'.join(numbered_lines), line_index
            return numbered_line[:VIEW_FILE_MAX_CHARS], line_index + 1
        numbered_lines.append(numbered_line)
        used_chars += len(numbered_line) + 1
    return '\n'.join(numbered_lines), end


def _view_file_section(file, content: str, page, sheet, offset: int, max_chars: int, line_numbers: bool) -> dict:
    """One page or sheet of a file, paged by offset inside it."""
    from open_webui.utils.file_structure import get_structure, page_range, sheet_range

    structure = get_structure(file)
    section = {}
    if page is not None:
        page = int(page)
        found = page_range(structure, page)
        if not found:
            pages = len(structure.get('pages') or [])
            return {
                'error': f'Page {page} is not available.'
                + (f' The file has {pages} pages.' if pages else ' This file has no page information; read it by offset.')
            }
        start, end = found
        section['page'] = page
        if page in (structure.get('low_quality_pages') or []):
            section['ocr_quality'] = 'low'
            section['hint'] = 'The OCR text of this page is unreliable; use view_pdf_page to see the page image.'
    else:
        found = sheet_range(structure, sheet)
        if not found:
            names = [name for name, _, _ in structure.get('sheets') or []]
            return {
                'error': f'Sheet "{sheet}" not found.'
                + (f' Sheets: {", ".join(names)}.' if names else ' This file has no sheet information; read it by offset.')
            }
        name, start, end = found
        section['sheet'] = name

    window = content[start:end]
    sliced = window[offset : offset + max_chars]
    if line_numbers:
        first_line = content[: start + offset].count('\n') + 1
        sliced = '\n'.join(f'{first_line + i}: {line}' for i, line in enumerate(sliced.split('\n')))
    result = {
        'id': file.id,
        'filename': file.filename,
        **section,
        'content': sliced,
        'section_chars': len(window),
    }
    if offset + max_chars < len(window):
        result['truncated'] = True
        result['next_offset'] = offset + max_chars
    return result


async def _read_budget_exhausted(metadata: Optional[dict]) -> Optional[str]:
    """Message when this answer has already read its budget of file text (files.read_budget_chars), else None."""
    from open_webui.file_pipeline.settings import file_settings, read_budget_chars

    budget = read_budget_chars(await file_settings())
    if not budget or metadata is None:
        return None
    used = metadata.get('file_read_chars', 0)
    if used < budget:
        return None
    return JSONCodec.dumps(
        {
            'error': f'Read limit reached: this answer has already read {used} characters of file text. '
            'Answer now from what you have read (or from a summarize_chat_file result). Read more only in a '
            'new answer, when the user asks about a specific part.'
        }
    )


def _charge_read(metadata: Optional[dict], text: str) -> None:
    if metadata is not None and text:
        metadata['file_read_chars'] = metadata.get('file_read_chars', 0) + len(text)


async def _readable_file(file_id: str, user: dict):
    from open_webui.models.files import Files

    file = await Files.get_file_by_id(file_id) if isinstance(file_id, str) and file_id else None
    if not file or not await _has_read_access_to_file(file, user):
        return None
    return file


async def list_available_skills(__metadata__: dict = None) -> str:
    """
    List the skills available in this chat with their id, name and description. Open one with view_skill.

    :return: The available skills
    """
    return (__metadata__ or {}).get('skill_manifest') or 'No skills are available.'


async def file_outline(
    file_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Show the structure of an attached file before reading it: headings (parts, chapters, sections, articles)
    with their page, the number of pages, spreadsheet sheets with their size, and pages whose OCR text is
    unreliable. Use the offsets, pages and sheet names with view_file.

    :param file_id: The ID of the file
    :return: JSON outline of the file
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        from open_webui.utils.file_structure import build_outline, get_structure

        file = await _readable_file(file_id, __user__)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})
        content = (file.data or {}).get('content') or ''
        outline = await asyncio.to_thread(build_outline, content, get_structure(file))
        return JSONCodec.dumps({'id': file.id, 'filename': file.filename, **outline}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'file_outline error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def _summary_completion(request: Request, user: UserModel, model_id: str, content, disable_thinking: bool) -> str:
    from open_webui.utils.chat import generate_chat_completion
    from open_webui.utils.context_compaction import _response_text
    from open_webui.utils.payload import apply_params_to_form_data

    models = request.app.state.MODELS
    params = await Config.get('task.model.params') or {}
    params = {key: value for key, value in params.items() if value is not None and value != ''} if isinstance(params, dict) else {}
    # Streamed, so proxies that cut a request after ~100 s without a response byte (RunPod, Cloudflare 524)
    # do not cut a long part summary.
    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': content}],
        'stream': True,
        'metadata': {
            **(request.state.metadata if hasattr(request.state, 'metadata') else {}),
            'task': 'file_summary',
        },
    }
    payload = apply_params_to_form_data(payload, models[model_id], params)
    if disable_thinking:
        payload['chat_template_kwargs'] = {**(payload.get('chat_template_kwargs') or {}), 'enable_thinking': False}
    response = await generate_chat_completion(request, form_data=payload, user=user)
    iterator = getattr(response, 'body_iterator', None)
    if iterator is None:
        return _response_text(response)
    parts, buffer = [], ''
    async for chunk in iterator:
        buffer += chunk.decode('utf-8', 'replace') if isinstance(chunk, bytes) else str(chunk)
        *lines, buffer = buffer.split('\n')
        for line in lines:
            line = line.strip()
            if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                continue
            try:
                data = JSONCodec.loads(line[5:].strip())
            except Exception:
                continue
            for choice in data.get('choices') or []:
                if (choice.get('delta') or {}).get('content'):
                    parts.append(choice['delta']['content'])
    return ''.join(parts)


# Tasks started by a tool that outlive its call (kept referenced until done).
_BACKGROUND_TASKS: set = set()
# (file id, summary cache key) -> the background task finishing that summary after an answer stopped waiting.
_SUMMARY_RUNS: dict = {}


async def summarize_chat_file(
    file_id: str,
    focus: Optional[str] = None,
    detail: str = 'normal',
    section: Optional[str] = None,
    part: int = 1,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_emitter__: callable = None,
) -> str:
    """
    Summarize a whole attached file without leaving parts out. The server reads the file part by part
    (pages with unreliable OCR are read from their images when the model supports vision) and keeps three
    levels with page references: brief (whole file), normal (per chapter/section) and full (per part).
    Use it for long files; files whose full text is already in the message can be read directly. The result
    is complete: answer from it without re-reading the file. A long result comes in parts: answer from the
    part you got and invite the user to type "tiếp" for the next one. A large file just uploaded takes minutes:
    the call waits until the summary is done (the user sees the progress), so call it once and wait.

    :param file_id: The ID of the file to summarize
    :param focus: Optional topic the user asked about; the summary still covers the whole file, the result says to answer about this topic
    :param detail: brief (short overview), normal (per chapter, default) or full (most detailed)
    :param section: Optional chapter/section title or page ("Chương II", "trang 12") to summarize only that part
    :param part: Part number of a long result (1 first; then 2, 3... when the user types "tiếp")
    :return: JSON with the summary (this part), part/parts_total, the covered sections and pages
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        from open_webui.file_pipeline.settings import file_settings
        from open_webui.file_pipeline.summary import cache_key, cached_summary, store_summary, summarize_file
        from open_webui.utils.file_structure import get_structure, is_pdf

        file = await _readable_file(file_id, __user__)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})
        if not ((file.data or {}).get('content') or '').strip():
            return JSONCodec.dumps({'error': 'The file has no extracted text'})
        # The summary is always the whole-file one (made once, in the background, shared by every question). A focus
        # only tells the answering model what to bring forward: summarizing again per focus re-read the whole file
        # each time a model wrote "tóm tắt nội dung" there (13 calls of 150 s on a 269-page file).
        emphasis = focus.strip() if isinstance(focus, str) and focus.strip().lower() not in ('', 'none', 'null') else None
        focus = None
        settings = await file_settings()

        # One result per answer. Models paged through part 2, 3... or asked again for an unfinished summary in the
        # same answer, each call waiting up to files.summary.wait_seconds (35 minutes for one answer on W).
        turn_key = (file.id, detail, (section or '').strip().lower())
        given = __metadata__.setdefault('summary_parts_given', {}) if __metadata__ is not None else {}
        if turn_key in given:
            return JSONCodec.dumps(
                {
                    'note': f'Part {given[turn_key]} of this summary is already in this answer: answer from it now. '
                    'Do not call summarize_chat_file again in this answer; the user types "tiếp" for the next part '
                    'or asks again later for a summary still being made.'
                },
                ensure_ascii=False,
            )

        def deliver(result: dict, cached: bool) -> str:
            from open_webui.file_pipeline.summary import paginate, select_view

            if not result.get('complete', True) and not result.get('parts_read'):
                # Nothing summarized yet (a large file just uploaded). Given the headings, models wrote a "summary"
                # of the table of contents and passed it off as the file's; they get no headings to do that with.
                given[turn_key] = 1
                return JSONCodec.dumps(
                    {
                        'id': result.get('id'),
                        'filename': result.get('filename'),
                        **({'pages': result['pages']} if result.get('pages') else {}),
                        'complete': False,
                        'parts_read': 0,
                        'file_parts_total': result.get('parts_total'),
                        'note': 'No part of this file is summarized yet: it is being summarized in the background '
                        f'(0 of {result.get("parts_total", 0)} parts done). Do not write a summary from the title '
                        'page, the headings or the first pages, and do not save a note. Tell the user in one or two '
                        'sentences that the file is still being summarized and to ask again in a few minutes.',
                    },
                    ensure_ascii=False,
                )

            pages = paginate(select_view(result, detail, section), settings['files.summary.page_chars'])
            index = min(max(int(part or 1), 1), len(pages))
            complete = result.get('complete', True)
            if complete:
                note = (
                    'This summary covers every part of the file in order, with page references [tr.N]. Answer from '
                    'it now; do not read the file again to check it.'
                )
            else:
                missing = ', '.join(result.get('pending_pages') or [])
                read, total = result.get('parts_read', 0), result.get('parts_total', 0)
                note = (
                    (
                        f'This summary is NOT complete yet: {read} of {total} parts are summarized, in order, with '
                        'page references [tr.N]'
                        + (f'; still being summarized in the background: {missing}' if missing else '')
                        + '. Answer now from these parts, say plainly which pages are not covered yet, and suggest '
                        'asking again in a few minutes for the rest.'
                        if read < total
                        else 'Every part of the file is summarized below, in order, with page references [tr.N]; the '
                        'shorter whole-file version is still being made in the background. Answer now from these parts.'
                    )
                    + ' Do not call summarize_chat_file again or read the file again in this answer.'
                )
            if index < len(pages):
                note += (
                    f' This is part {index}/{len(pages)}. Stop here: answer from this part now and invite the user '
                    f'to type "tiếp". Only when they do, in a later message, call summarize_chat_file with '
                    f'part={index + 1} and the same detail/section.'
                )
            if emphasis:
                note += (
                    f' The user asked about: {emphasis}. Answer about that from this summary; for exact wording or '
                    'details it leaves out, use query_chat_files.'
                )
            given[turn_key] = index
            answer = {
                'id': result.get('id'),
                'filename': result.get('filename'),
                'detail': detail,
                **({'section': section} if section else {}),
                'part': index,
                'parts_total': len(pages),
                'parts_read': result.get('parts_read'),
                **({'complete': False, 'file_parts_total': result.get('parts_total')} if not complete else {}),
                **({'pages': result['pages']} if result.get('pages') else {}),
                'coverage': result.get('coverage'),
                **({'cached': True} if cached else {}),
                'summary': pages[index - 1],
                'note': note,
            }
            # What the summary adds to the answer's context counts toward its read budget like file text.
            _charge_read(__metadata__, pages[index - 1])
            return JSONCodec.dumps(answer, ensure_ascii=False)

        cached = cached_summary(file, focus)
        if cached:
            return deliver(cached, True)

        async def emit(description: str, finished: bool = False):
            if __event_emitter__:
                await __event_emitter__({'type': 'status', 'data': {'description': description, 'done': finished}})

        from open_webui.file_pipeline.models import FileJobs
        from open_webui.file_pipeline.summary import cached_parts, ensure_plan, store_part
        from open_webui.models.files import Files
        from open_webui.utils.file_structure import is_spreadsheet

        # The answer waits for the whole summary: people upload a file and ask for its summary right away, they do
        # not come back later. Only a summary that stops moving (no new part for files.summary.wait_seconds: the
        # model or the job is stuck) is given up, and the answer is written from the parts made by then.
        stall_seconds = settings['files.summary.wait_seconds']
        structure = get_structure(file)
        segment_chars = settings['files.summary.segment_chars']
        plan = await ensure_plan(file, structure, segment_chars)
        common = dict(
            focus=focus,
            segment_chars=segment_chars,
            concurrency=settings['files.summary.concurrency'],
            max_chars=settings['files.summary.max_chars'],
            ratio_pct=settings['files.summary.ratio_pct'],
            min_chars=settings['files.summary.min_chars'],
            plan=plan,
        )

        async def unused_model(message) -> str:
            raise RuntimeError('no model call expected')

        async def parts_made() -> dict:
            """The part summaries already made, without asking the model for anything."""
            current = await Files.get_file_by_id(file.id) or file
            made = await summarize_file(
                current,
                get_structure(current),
                unused_model,
                **common,
                part_cache=cached_parts(current),
                ready=lambda pages: False,
                parts_only=True,
            )
            made.pop('running', None)
            return made

        # An earlier answer left this summary running: wait for that run instead of starting the same work again.
        run_key = (file.id, cache_key((file.data or {}).get('content') or '', file.hash, focus))
        earlier = _SUMMARY_RUNS.get(run_key)
        if earlier and not earlier.done():
            await emit(f'Đang chờ bản tóm tắt {file.filename} đang làm dở')
            await asyncio.wait({earlier})
            cached = cached_summary(await Files.get_file_by_id(file.id) or file, focus)
            if cached:
                await emit(f'Đã có bản tóm tắt {file.filename}', True)
                return deliver(cached, True)
            result = await parts_made()
            await emit(f'Dùng {result.get("parts_read", 0)}/{result.get("parts_total", 0)} phần đã tóm tắt của {file.filename}', True)
            return deliver(result, True)

        # The background pipeline is summarizing this file (or is about to): it sends every part to the model
        # already, so wait for it instead of summarizing the same text a second time, then use its parts.
        background = (
            focus is None
            and settings['files.summary.precompute']
            and len((file.data or {}).get('content') or '') >= settings['files.summary.precompute_min_chars']
            and not is_spreadsheet(file.filename, (file.meta or {}).get('content_type'))
        )
        if background:
            job = await FileJobs.get(file.id)
            seen, moved_at, shown = None, time.monotonic(), ''
            while job and job.status in ('queued', 'running'):
                # Progress: the job's stage and counter, and the part summaries kept on the file.
                progress = (job.status, job.stage, JSONCodec.dumps(job.progress or {}), len(cached_parts(file)))
                if progress != seen:
                    seen, moved_at = progress, time.monotonic()
                elif time.monotonic() - moved_at > stall_seconds:
                    log.warning('summarize_chat_file: no progress on %s for %ss; answering from what is made', file.id, stall_seconds)
                    break
                label = (job.progress or {}).get('label') if job.status == 'running' else 'đang chờ đến lượt xử lý'
                status_text = f'Đang tóm tắt {file.filename}: {label or "đang chuẩn bị"}'
                if status_text != shown:
                    shown = status_text
                    await emit(status_text)
                await asyncio.sleep(3)
                file = await Files.get_file_by_id(file.id)
                cached = cached_summary(file, None)
                if cached:
                    await emit(f'Đã có bản tóm tắt {file.filename}', True)
                    return deliver(cached, True)
                job = await FileJobs.get(file.id)
            if job and job.status == 'running':
                # parts_only: with every part made, the merge is the background job's (it raised "no model call
                # expected" here).
                result = await parts_made()
                await emit(f'Dùng {result.get("parts_read", 0)}/{result.get("parts_total", 0)} phần đã tóm tắt của {file.filename}', True)
                return deliver(result, True)
            file = await Files.get_file_by_id(file.id)
            cached = cached_summary(file, focus)
            if cached:
                return deliver(cached, True)
            structure = get_structure(file)

        models = __request__.app.state.MODELS
        chat_model = settings['files.summary.chat_model']
        model_id = chat_model if chat_model in models else (__metadata__ or {}).get('model_id')
        if model_id not in models:
            return JSONCodec.dumps({'error': 'No model available to summarize the file'})

        user = UserModel(**__user__)
        vision = (models[model_id].get('info', {}).get('meta', {}).get('capabilities') or {}).get('vision', True)
        low_pages = set(structure.get('low_quality_pages') or [])
        pdf_path = None
        if vision and low_pages and file.path and is_pdf(file.filename, (file.meta or {}).get('content_type')):
            from open_webui.storage.provider import Storage

            pdf_path = await asyncio.to_thread(Storage.get_file, file.path)

        file_id_value = file.id
        result = await summarize_file(
            file,
            structure,
            lambda message: _summary_completion(
                __request__, user, model_id, message, settings['files.summary.disable_thinking']
            ),
            **common,
            image_max_side=settings['files.page_image_max_side'],
            pdf_path=pdf_path,
            image_pages=low_pages,
            emit=emit,
            part_cache=cached_parts(file),
            save_part=lambda key, text: store_part(file_id_value, key, text),
        )
        running = result.pop('running', None) or []
        if result.get('complete', True):
            await store_summary(file, focus, result)
            await emit(f'Đã tóm tắt xong {file.filename}', True)
            return deliver(result, False)
        job_can_finish = focus is None and not is_spreadsheet(file.filename, (file.meta or {}).get('content_type'))
        if running or job_can_finish:
            # The rest is finished in the background, so the next question gets the whole summary: a merge still
            # running here is kept when it ends; parts still running are left for the background job.
            async def finish_in_background() -> None:
                outcomes = await asyncio.gather(*running, return_exceptions=True) if running else []
                whole = next((o for o in outcomes if isinstance(o, dict) and o.get('tiers') and o.get('complete', True)), None)
                if whole:
                    await store_summary(await Files.get_file_by_id(file_id_value) or file, focus, whole)
                elif job_can_finish:
                    await FileJobs.enqueue(file_id_value, {'summary_only': True})

            task = asyncio.create_task(finish_in_background())
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)
            _SUMMARY_RUNS[run_key] = task
            task.add_done_callback(lambda done, key=run_key: _SUMMARY_RUNS.pop(key, None) if _SUMMARY_RUNS.get(key) is done else None)
        await emit(f'Đã tóm tắt {result.get("parts_read", 0)}/{result.get("parts_total", 0)} phần của {file.filename}', True)
        return deliver(result, False)
    except Exception as e:
        log.exception(f'summarize_chat_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


_TABLE_CACHE: dict = {}  # file id -> (hash, monotonic time, sheets); a few recent spreadsheets


async def _spreadsheet_grid(file) -> list[dict]:
    from open_webui.file_pipeline.tables import read_grid
    from open_webui.storage.provider import Storage

    cached = _TABLE_CACHE.get(file.id)
    if cached and cached[0] == file.hash and time.monotonic() - cached[1] < 600:
        return cached[2]
    path = await asyncio.to_thread(Storage.get_file, file.path)
    sheets = await asyncio.to_thread(read_grid, path, file.filename)
    if len(_TABLE_CACHE) >= 4:
        _TABLE_CACHE.pop(min(_TABLE_CACHE, key=lambda key: _TABLE_CACHE[key][1]))
    _TABLE_CACHE[file.id] = (file.hash, time.monotonic(), sheets)
    return sheets


async def query_table(
    file_id: str,
    sheet: Optional[str] = None,
    filters: Optional[list[dict]] = None,
    columns: Optional[list[str]] = None,
    sort_by: Optional[str] = None,
    descending: bool = False,
    limit: int = 50,
    group_by: Optional[str] = None,
    header_row: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Read a spreadsheet (xlsx, csv) attached to the chat cell by cell: any sheet, columns by header name or letter
    (A, B, R), each row returned with its row number in the sheet, so a value can be located exactly (e.g. sheet
    "Kanban", column A, row 2). Also filters, sorts, picks columns, or counts rows per value. Exact, fast and done
    on the server: this is how to read a spreadsheet by sheet, column and cell, and to check that it can be read
    that way, for questions like "tasks due in December", "rows where Owner is SOC", "how many per status".
    Dates and numbers compare as values (30/11/2026 and 2026-11-30 are the same date).

    :param file_id: The ID of the spreadsheet file
    :param sheet: Sheet name or 1-based number (default: the first sheet); see the sheets listed for the file
    :param filters: Conditions all rows must meet: [{"column": "Hạn hoàn thành", "op": "between", "value": ["2026-12-01", "2026-12-31"]}]. column: header name or letter (A, B); op: eq, ne, contains, not_contains, gt, gte, lt, lte, between (value [from, to]), in (value list), empty, not_empty
    :param columns: Optional columns to return (default: all non-empty)
    :param sort_by: Optional column to sort by
    :param descending: Sort from largest/latest first
    :param limit: Maximum rows to return (default 50, max 500)
    :param group_by: Instead of rows, count the matching rows per value of this column
    :param header_row: 1-based row of the column headers when the automatic guess is wrong
    :return: JSON with the sheet, header row, columns, number of matching rows and the rows (with their sheet row number)
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        from open_webui.file_pipeline.settings import file_settings
        from open_webui.file_pipeline.tables import query
        from open_webui.utils.file_structure import is_spreadsheet

        file = await _readable_file(file_id, __user__)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})
        if not file.path or not is_spreadsheet(file.filename, (file.meta or {}).get('content_type')):
            return JSONCodec.dumps({'error': 'query_table only reads spreadsheets (xlsx, csv); use view_file for documents'})
        if isinstance(filters, dict):
            filters = [filters]
        if isinstance(filters, str):
            try:
                filters = JSONCodec.loads(filters)
            except Exception:
                return JSONCodec.dumps({'error': 'filters must be a JSON list of {column, op, value}'})
        if isinstance(columns, str):
            columns = [c.strip() for c in columns.split(',') if c.strip()]
        sheets = await _spreadsheet_grid(file)
        settings = await file_settings()
        result = await asyncio.to_thread(
            query,
            sheets,
            sheet,
            filters if isinstance(filters, list) else None,
            columns,
            sort_by,
            bool(descending),
            int(limit or 50),
            group_by,
            int(header_row) if str(header_row or '').strip().isdigit() else None,
            settings['files.query_max_chars'],
        )
        result = {'file': file.filename, 'sheets': [s['name'] for s in sheets], **result}
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_table error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_pdf_page(
    file_id: str,
    page: int,
    __request__: Request = None,
    __user__: dict = None,
):
    """
    Look at one page of an attached PDF as an image. Use it for pages listed in low_quality_pages (their OCR
    text is unreliable) or to check tables, stamps, signatures and layout. One page per call.

    :param file_id: The ID of the PDF file
    :param page: 1-indexed page number
    :return: The page image and the text extracted from that page
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        from open_webui.storage.provider import Storage
        from open_webui.utils.file_structure import get_structure, is_pdf, page_range, render_pdf_page

        page = int(page)
        file = await _readable_file(file_id, __user__)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})
        if not file.path or not is_pdf(file.filename, (file.meta or {}).get('content_type')):
            return JSONCodec.dumps({'error': 'view_pdf_page only works on PDF files'})
        path = await asyncio.to_thread(Storage.get_file, file.path)
        from open_webui.file_pipeline.settings import file_settings

        max_side = (await file_settings())['files.page_image_max_side']
        image, page_count = await asyncio.to_thread(render_pdf_page, path, page, max_side)
        structure = get_structure(file)
        found = page_range(structure, page)
        text = ((file.data or {}).get('content') or '')[found[0] : found[1]][:3000] if found else ''
        return {
            'id': file.id,
            'filename': file.filename,
            'page': page,
            'page_count': page_count,
            'image': image,
            **({'extracted_text': text} if text else {}),
            'note': 'The extracted text comes from OCR and may contain errors; the image is authoritative.',
        }
    except ValueError as e:
        return JSONCodec.dumps({'error': str(e)})
    except Exception as e:
        log.exception(f'view_pdf_page error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def open_in_terminal(
    file_id: str,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """
    Copy an attached file into the terminal so code can work on the original file: pandas analysis, formulas
    and formatting, or filling a Word/Excel report template (reading a spreadsheet's cells and rows is
    query_table). Attached files are not in the terminal before this call. Then use the returned terminal_path
    in terminal commands, and deliver created files with attach_terminal_file.

    :param file_id: The ID of the attached file
    :return: JSON with the file's terminal_path
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    metadata = __metadata__ or {}
    if not metadata.get('terminal_id'):
        return JSONCodec.dumps({'error': 'No terminal is available in this chat'})
    try:
        from pathlib import Path
        from urllib.parse import quote

        from open_webui.utils.terminals import get_terminal_json

        file = await _readable_file(file_id, __user__)
        if not file or not file.path:
            return JSONCodec.dumps({'error': 'File not found'})
        user = UserModel(**__user__)
        extra_params = {'__event_call__': __event_call__}
        cwd = await get_terminal_json(__request__, user, metadata, '/files/cwd', extra_params)
        cwd = cwd.get('cwd') if isinstance(cwd, dict) else None
        if not cwd:
            return JSONCodec.dumps({'error': 'Terminal working directory unavailable'})
        filename = Path(file.filename or 'file')
        stem = re.sub(r'[^\w.-]+', '_', filename.stem, flags=re.UNICODE).strip('_') or 'file'
        name = f'{stem}_{file.id[:8]}{filename.suffix}'
        upload = {'id': file.id, 'name': name}
        result = None
        # A per-user folder keeps attachments apart; fall back to the working directory if it cannot be used.
        for directory in (f'{cwd.rstrip("/")}/{TERMINAL_ATTACHMENTS_DIR}/{user.id}', cwd):
            result = await get_terminal_json(
                __request__,
                user,
                metadata,
                f'/files/upload?directory={quote(directory, safe="")}',
                extra_params,
                file=upload,
                verified_access=True,
            )
            if isinstance(result, dict) and isinstance(result.get('path'), str) and result['path']:
                break
            result = None
        if not result:
            return JSONCodec.dumps({'error': 'Upload to the terminal failed'})
        return JSONCodec.dumps(
            {
                'id': file.id,
                'filename': file.filename,
                'terminal_path': result['path'],
                'note': 'This is a copy of the original file; read it with code (e.g. pandas, openpyxl, python-docx).',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'open_in_terminal error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    page: Optional[int] = None,
    sheet: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
    __metadata__: dict = None,
) -> str:
    """
    Get the content of a file by its ID. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0); with page or sheet, relative to it
    :param max_chars: Maximum characters to return (a server-side hard cap applies)
    :param line_numbers: If true, prefix each line with its 1-indexed line number
    :param start_line: Optional 1-indexed start line (overrides offset/max_chars when set)
    :param end_line: Optional 1-indexed end line (inclusive; reads from line 1 if no start line is given)
    :param page: Optional 1-indexed page of a PDF or document to read (see file_outline)
    :param sheet: Optional spreadsheet sheet name (or 1-indexed number) to read
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = VIEW_FILE_DEFAULT_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), VIEW_FILE_MAX_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.models.files import Files

        file = await Files.get_file_by_id(file_id)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})

        if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
            return JSONCodec.dumps({'error': 'File not found'})
        if exhausted := await _read_budget_exhausted(__metadata__):
            return exhausted

        content = ''
        if file.data:
            content = file.data.get('content', '')

        if isinstance(page, str):
            page = int(page) if page.strip().isdigit() else None
        if isinstance(sheet, str) and sheet.strip().lower() in ('', 'none', 'null'):
            sheet = None
        if page is not None or sheet:
            section = _view_file_section(file, content, page, sheet, offset, max_chars, line_numbers)
            _charge_read(__metadata__, section.get('content') or '')
            return JSONCodec.dumps(section, ensure_ascii=False)

        total_chars = len(content)

        # Line-based addressing (overrides char-based offset/max_chars)
        if start_line is not None or end_line is not None:
            all_lines = content.split('\n')
            total_lines = len(all_lines)
            start_index = max(1, int(start_line or 1)) - 1  # 1-indexed to 0-indexed
            end_index = min(total_lines, int(end_line) if end_line else start_index + 100)
            sliced, end_index = _numbered_lines_within_cap(all_lines, start_index, end_index)
            is_truncated = end_index < total_lines
            result = {
                'id': file.id,
                'filename': file.filename,
                'content': sliced,
                'updated_at': file.updated_at,
                'created_at': file.created_at,
                'total_lines': total_lines,
                'showing_lines': f'{start_index + 1}-{end_index}',
            }
            if is_truncated:
                result['truncated'] = True
                result['next_start_line'] = end_index + 1
            _charge_read(__metadata__, result['content'])
            return JSONCodec.dumps(result, ensure_ascii=False)

        sliced = content[offset : offset + max_chars]
        returned_chars = len(sliced)
        is_truncated = (offset + returned_chars) < total_chars

        if line_numbers:
            start_ln = content[:offset].count('\n') + 1
            lines = sliced.split('\n')
            sliced = '\n'.join(f'{start_ln + i}: {line}' for i, line in enumerate(lines))

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = returned_chars
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + returned_chars

        _charge_read(__metadata__, result['content'])
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_knowledge_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the content of a file from a knowledge base. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0)
    :param max_chars: Maximum characters to return (a server-side hard cap applies)
    :param line_numbers: If true, prefix each line with its 1-indexed line number
    :param start_line: Optional 1-indexed start line (overrides offset/max_chars when set)
    :param end_line: Optional 1-indexed end line (inclusive; reads from line 1 if no start line is given)
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = VIEW_FILE_DEFAULT_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), VIEW_FILE_MAX_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        file = await Files.get_file_by_id(file_id)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})

        # Check access via any KB containing this file
        knowledges = await Knowledges.get_knowledges_by_file_id(file_id)
        has_knowledge_access = False
        knowledge_info = None

        for knowledge_base in knowledges:
            if (
                user_role == 'admin'
                or knowledge_base.user_id == user_id
                or await AccessGrants.has_access(
                    user_id=user_id,
                    resource_type='knowledge',
                    resource_id=knowledge_base.id,
                    permission='read',
                    user_group_ids=set(user_group_ids),
                )
            ):
                has_knowledge_access = True
                knowledge_info = {'id': knowledge_base.id, 'name': knowledge_base.name}
                break

        if not has_knowledge_access:
            if file.user_id != user_id and user_role != 'admin':
                return JSONCodec.dumps({'error': 'Access denied'})

        content = ''
        if file.data:
            content = file.data.get('content', '')

        total_chars = len(content)

        # Line-based addressing (overrides char-based offset/max_chars)
        if start_line is not None or end_line is not None:
            all_lines = content.split('\n')
            total_lines = len(all_lines)
            start_index = max(1, int(start_line or 1)) - 1
            end_index = min(total_lines, int(end_line) if end_line else start_index + 100)
            sliced, end_index = _numbered_lines_within_cap(all_lines, start_index, end_index)
            is_truncated = end_index < total_lines
            result = {
                'id': file.id,
                'filename': file.filename,
                'content': sliced,
                'updated_at': file.updated_at,
                'created_at': file.created_at,
                'total_lines': total_lines,
                'showing_lines': f'{start_index + 1}-{end_index}',
            }
            if knowledge_info:
                result['knowledge_id'] = knowledge_info['id']
                result['knowledge_name'] = knowledge_info['name']
            if is_truncated:
                result['truncated'] = True
                result['next_start_line'] = end_index + 1
            return JSONCodec.dumps(result, ensure_ascii=False)

        sliced = content[offset : offset + max_chars]
        returned_chars = len(sliced)
        is_truncated = (offset + returned_chars) < total_chars

        if line_numbers:
            start_ln = content[:offset].count('\n') + 1
            lines = sliced.split('\n')
            sliced = '\n'.join(f'{start_ln + i}: {line}' for i, line in enumerate(lines))

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }
        if knowledge_info:
            result['knowledge_id'] = knowledge_info['id']
            result['knowledge_name'] = knowledge_info['name']

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = returned_chars
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + returned_chars

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_knowledge_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def display_file(
    path: str,
    page: int | None = None,
    inline: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] | None = None,
) -> str:
    """
    Show a terminal, chat, or knowledge base file to the user.
    After display_file succeeds, do not display the same file again or emit Markdown for it.

    :param path: Terminal file path, or file_id or /api/v1/files/<file_id>/content for a chat or knowledge base file
    :param page: For PDF, DOCX, and PPTX files, open the preview at this 1-based page or slide number
    :param inline: Show terminal files inline instead of opening the file viewer; uploaded files always display inline
    :return: JSON with the file's path, name, content type, and display state
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available', 'exists': False})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available', 'exists': False})

    try:
        from open_webui.models.files import Files

        if '/' not in path:
            path = f'/api/v1/files/{path}/content'
        parts = path.split('/')
        if len(parts) != 6 or parts[:4] != ['', 'api', 'v1', 'files'] or parts[-1] != 'content':
            return JSONCodec.dumps(
                {'error': 'Use file_id or /api/v1/files/<file_id>/content for an uploaded file.', 'exists': False}
            )

        file = await Files.get_file_by_id(parts[4])
        if not file:
            return JSONCodec.dumps({'error': 'File not found', 'exists': False})

        if not await _has_read_access_to_file(file, __user__, __model_knowledge__):
            return JSONCodec.dumps({'error': 'File not found', 'exists': False})

        file_meta = file.meta or {}
        result = {
            'type': 'file',
            'source': 'open_webui',
            'exists': True,
            'displayed': True,
            'path': path,
            'full_path': path,
            'name': file_meta.get('name') or file.filename,
            'content_type': file_meta.get('content_type') or 'application/octet-stream',
            'mime_type': file_meta.get('content_type') or 'application/octet-stream',
        }
        if page:
            result['page'] = page
        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'display_file error: {e}')
        return JSONCodec.dumps({'error': str(e), 'exists': False})


async def list_knowledge(
    knowledge_id: Optional[str] = None,
    skip: int = 0,
    count: int = 50,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: Optional[list[dict]] = None,
) -> str:
    """
    List knowledge bases, files, and notes attached to the current model.
    Use this first to discover what knowledge is available before querying or reading files.
    Without knowledge_id: returns KB summaries (name, description, file_count)
    plus standalone files and notes — no file listing inside KBs.
    With knowledge_id: includes paginated file listing for that specific KB.
    Use skip/count to page through large KBs.

    :param knowledge_id: Optional KB ID to get file listing for
    :param skip: Number of files to skip for pagination (default: 0)
    :param count: Maximum files per page (default: 50, max: 200)
    :return: JSON with knowledge_bases, files, and notes attached to this model
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not __model_knowledge__:
        return JSONCodec.dumps({'knowledge_bases': [], 'files': [], 'notes': []})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(skip, str):
        try:
            skip = int(skip)
        except ValueError:
            skip = 0
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 50
    if isinstance(knowledge_id, str) and knowledge_id.lower() in ('none', 'null', ''):
        knowledge_id = None

    count = min(count, 200)

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        knowledge_bases = []
        files = []
        notes = []

        for item in __model_knowledge__:
            item_type = item.get('type')
            item_id = item.get('id')

            if item_type == 'collection':
                knowledge = await Knowledges.get_knowledge_by_id(item_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    kb_files = await Knowledges.get_files_by_id(knowledge.id)
                    file_count = len(kb_files) if kb_files else 0

                    kb_entry = {
                        'id': knowledge.id,
                        'name': knowledge.name,
                        'description': knowledge.description or '',
                        'file_count': file_count,
                    }

                    # Include file listing only when this KB is targeted
                    if knowledge_id and knowledge_id == knowledge.id:
                        if kb_files:
                            paged_files = kb_files[skip : skip + count]
                            kb_entry['files'] = [{'id': f.id, 'filename': f.filename} for f in paged_files]
                            kb_entry['files_skip'] = skip
                            kb_entry['files_count'] = len(paged_files)
                            kb_entry['files_total'] = file_count
                            kb_entry['has_more'] = skip + count < file_count

                    knowledge_bases.append(kb_entry)

            elif item_type == 'file':
                file = await Files.get_file_by_id(item_id)
                if file:
                    files.append(
                        {
                            'id': file.id,
                            'filename': file.filename,
                            'updated_at': file.updated_at,
                        }
                    )

            elif item_type == 'note':
                note = await Notes.get_note_by_id(item_id)
                if note and (
                    user_role == 'admin'
                    or note.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='note',
                        resource_id=note.id,
                        permission='read',
                    )
                ):
                    notes.append(
                        {
                            'id': note.id,
                            'title': note.title,
                        }
                    )

        return JSONCodec.dumps(
            {
                'knowledge_bases': knowledge_bases,
                'files': files,
                'notes': notes,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_knowledge error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_files(
    query: str,
    knowledge_ids: Optional[list[str]] = None,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
    __model_knowledge__: list[dict] = None,
) -> str:
    """
    Search knowledge base files using semantic/vector search. Searches across collections (KBs),
    individual files, and notes that the user has access to.
    Helpful for internal documentation, uploaded knowledge, and attached model knowledge.

    :param query: The search query to find semantically relevant content
    :param knowledge_ids: Optional list of KB ids to limit search to specific knowledge bases
    :param count: Maximum number of results to return (default: 5)
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            count = 5  # Default fallback

    # Handle knowledge_ids being string "None", "null", or empty
    if isinstance(knowledge_ids, str):
        if knowledge_ids.lower() in ('none', 'null', ''):
            knowledge_ids = None
        else:
            # Try to parse as JSON array if it looks like one
            try:
                knowledge_ids = JSONCodec.loads(knowledge_ids)
            except JSONCodec.JSONDecodeError:
                # Treat as single ID
                knowledge_ids = [knowledge_ids]

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.files import Files
        from open_webui.models.knowledge import Knowledges
        from open_webui.models.notes import Notes
        from open_webui.retrieval.external import retrieve_external_knowledge
        from open_webui.retrieval.utils import query_collection

        user_id = __user__.get('id')
        user_role = __user__.get('role', 'user')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]

        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)

        collection_names = []
        external_knowledges = []
        note_results = []  # Notes aren't vectorized, handle separately

        # If model has attached knowledge, use those
        if __model_knowledge__:
            for item in __model_knowledge__:
                item_type = item.get('type')
                item_id = item.get('id')

                if item_type == 'collection':
                    # Knowledge base - use KB ID as collection name
                    knowledge = await Knowledges.get_knowledge_by_id(item_id)
                    if knowledge and (
                        user_role == 'admin'
                        or knowledge.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='knowledge',
                            resource_id=knowledge.id,
                            permission='read',
                            user_group_ids=set(user_group_ids),
                        )
                    ):
                        if (knowledge.meta or {}).get('source') == 'external':
                            external_knowledges.append(knowledge)
                        else:
                            collection_names.append(item_id)

                elif item_type == 'file':
                    # Individual file - use file-{id} as collection name
                    file = await Files.get_file_by_id(item_id)
                    if file:
                        collection_names.append(f'file-{item_id}')

                elif item_type == 'note':
                    # Note - always return full content as context
                    note = await Notes.get_note_by_id(item_id)
                    if note and (
                        user_role == 'admin'
                        or note.user_id == user_id
                        or await AccessGrants.has_access(
                            user_id=user_id,
                            resource_type='note',
                            resource_id=note.id,
                            permission='read',
                        )
                    ):
                        content = note.data.get('content', {}).get('md', '')
                        note_results.append(
                            {
                                'content': content,
                                'source': note.title,
                                'note_id': note.id,
                                'type': 'note',
                            }
                        )

        elif knowledge_ids:
            # User specified specific KBs
            for knowledge_id in knowledge_ids:
                knowledge = await Knowledges.get_knowledge_by_id(knowledge_id)
                if knowledge and (
                    user_role == 'admin'
                    or knowledge.user_id == user_id
                    or await AccessGrants.has_access(
                        user_id=user_id,
                        resource_type='knowledge',
                        resource_id=knowledge.id,
                        permission='read',
                        user_group_ids=set(user_group_ids),
                    )
                ):
                    if (knowledge.meta or {}).get('source') == 'external':
                        external_knowledges.append(knowledge)
                    else:
                        collection_names.append(knowledge_id)
        else:
            # No model knowledge and no specific IDs - search all accessible KBs
            result = await Knowledges.search_knowledge_bases(
                user_id,
                filter={
                    'query': '',
                    'user_id': user_id,
                    'group_ids': user_group_ids,
                },
                skip=0,
                limit=50,
            )
            for knowledge_base in result.items:
                if (knowledge_base.meta or {}).get('source') == 'external':
                    external_knowledges.append(knowledge_base)
                else:
                    collection_names.append(knowledge_base.id)

        chunks = []

        # Add note results first
        chunks.extend(note_results)

        # Query vector collections if any
        if collection_names:
            query_results = await query_collection(
                __request__,
                collection_names=collection_names,
                queries=[query],
                embedding_function=lambda queries, prefix: embedding_function(queries, prefix=prefix, user=user_model),
                k=count,
                user=user_model,
            )

            if query_results and 'documents' in query_results:
                documents = query_results.get('documents', [[]])[0]
                metadatas = query_results.get('metadatas', [[]])[0]
                distances = query_results.get('distances', [[]])[0]

                file_ids = {metadata['file_id'] for metadata in metadatas if metadata.get('file_id')}
                if file_ids:
                    file_names = {
                        file.id: (file.meta or {}).get('name')
                        for file in await Files.get_file_metadatas_by_ids(list(file_ids))
                    }
                    for metadata in metadatas:
                        file_name = file_names.get(metadata.get('file_id'))
                        if file_name:
                            if metadata.get('source') == metadata.get('name'):
                                metadata['source'] = file_name
                            metadata['name'] = file_name

                for idx, doc in enumerate(documents):
                    chunk_info = {
                        **filter_source_metadata(metadatas[idx]),
                        'content': doc,
                        'source': metadatas[idx].get('source', metadatas[idx].get('name', 'Unknown')),
                        'file_id': metadatas[idx].get('file_id', ''),
                    }
                    if idx < len(distances):
                        chunk_info['distance'] = distances[idx]
                    chunks.append(chunk_info)

        for knowledge in external_knowledges:
            query_results = await retrieve_external_knowledge(
                __request__,
                knowledge,
                queries=[query],
                count=count,
                user=user_model,
            )
            documents = query_results.get('documents', [[]])[0]
            metadatas = query_results.get('metadatas', [[]])[0]
            distances = query_results.get('distances', [[]])[0]

            for idx, doc in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                chunk_info = {
                    **filter_source_metadata(metadata),
                    'content': doc,
                    'source': metadata.get('source', metadata.get('name', knowledge.name)),
                    'type': 'external',
                    'knowledge_id': knowledge.id,
                }
                if idx < len(distances):
                    chunk_info['distance'] = distances[idx]
                chunks.append(chunk_info)

        # Limit to requested count
        chunks = chunks[:count]

        return JSONCodec.dumps(chunks, ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_knowledge_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_knowledge_bases(
    query: str,
    count: int = 5,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search knowledge bases by semantic similarity to query.
    Finds KBs whose name/description match the meaning of your query.
    Helpful for discovering which knowledge base to query next.

    :param query: Natural language query describing what you're looking for
    :param count: Maximum results (default: 5)
    :return: JSON with matching KBs (id, name, description, similarity)
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        import heapq

        from open_webui.models.knowledge import Knowledges
        from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
        from open_webui.routers.knowledge import KNOWLEDGE_BASES_COLLECTION

        user_id = __user__.get('id')
        user_group_ids = [group.id for group in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})
        user_model = UserModel(**__user__)
        query_embedding = await embedding_function(
            query, prefix=await Config.get('rag.embedding_query_prefix'), user=user_model
        )

        # Min-heap of (distance, knowledge_base_id) - only holds top `count` results
        top_results_heap = []
        seen_ids = set()
        page_offset = 0
        page_size = 100

        while True:
            accessible_knowledge_bases = await Knowledges.search_knowledge_bases(
                user_id,
                filter={'user_id': user_id, 'group_ids': user_group_ids},
                skip=page_offset,
                limit=page_size,
            )

            if not accessible_knowledge_bases.items:
                break

            accessible_ids = [kb.id for kb in accessible_knowledge_bases.items]

            search_results = await ASYNC_VECTOR_DB_CLIENT.search(
                collection_name=KNOWLEDGE_BASES_COLLECTION,
                vectors=[query_embedding],
                filter={'knowledge_base_id': {'$in': accessible_ids}},
                limit=count,
            )

            if search_results and search_results.ids and search_results.ids[0]:
                result_ids = search_results.ids[0]
                result_distances = search_results.distances[0] if search_results.distances else [0] * len(result_ids)

                for knowledge_base_id, distance in zip(result_ids, result_distances):
                    if knowledge_base_id in seen_ids:
                        continue
                    seen_ids.add(knowledge_base_id)

                    if len(top_results_heap) < count:
                        heapq.heappush(top_results_heap, (distance, knowledge_base_id))
                    elif distance > top_results_heap[0][0]:
                        heapq.heapreplace(top_results_heap, (distance, knowledge_base_id))

            page_offset += page_size
            if len(accessible_knowledge_bases.items) < page_size:
                break
            if page_offset >= MAX_KNOWLEDGE_BASE_SEARCH_ITEMS:
                break

        # Sort by distance descending (best first) and fetch KB details
        sorted_results = sorted(top_results_heap, key=lambda x: x[0], reverse=True)

        matching_knowledge_bases = []
        for distance, knowledge_base_id in sorted_results:
            knowledge_base = await Knowledges.get_knowledge_by_id(knowledge_base_id)
            if knowledge_base:
                matching_knowledge_bases.append(
                    {
                        'id': knowledge_base.id,
                        'name': knowledge_base.name,
                        'description': knowledge_base.description or '',
                        'similarity': round(distance, 4),
                    }
                )

        return JSONCodec.dumps(matching_knowledge_bases, ensure_ascii=False)

    except Exception as e:
        log.exception(f'query_knowledge_bases error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Skills tools


async def view_skill(
    id: str,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read skill instructions and file list. Large skills return an opening preview and next_offset.

    :param id: Skill ID from the available skills manifest.
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request and user context required'})
    try:
        terminal_skill_prefix = 'terminal:'
        if isinstance(id, str) and id.startswith(terminal_skill_prefix):
            from open_webui.utils.terminals import get_terminal_skill

            skill_name = unquote(id.removeprefix(terminal_skill_prefix))
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                skill_name,
                {'__event_call__': __event_call__},
                overview=True,
            )
            if not skill:
                return JSONCodec.dumps({'error': f"Skill '{id}' not found"})
            return JSONCodec.dumps(skill, ensure_ascii=False)

        from types import SimpleNamespace

        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        actor = SimpleNamespace(**__user__)
        skill = await authorized_skill(id.lower(), actor)
        if not skill.is_active:
            await authorized_skill(skill.id, actor, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        return JSONCodec.dumps(
            {
                'id': skill.id,
                'name': snapshot['name'],
                **await skill_overview(snapshot['content'], file_summaries(snapshot['data']['files'])),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def read_skill_file(
    id: str,
    path: str,
    offset: int = 0,
    max_chars: int = 10000,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Read a skill file from the loaded snapshot. Terminal skills support SKILL.md only.

    :param id: Skill ID.
    :param path: Relative path within the skill.
    :param offset: Character offset for paging text.
    :param max_chars: Maximum characters to return, capped by the admin skill content limit (default 10000).
    """
    try:
        from types import SimpleNamespace
        from urllib.parse import urlencode

        from open_webui.models.skills import get_skill_snapshot
        from open_webui.routers.skills import authorized_skill
        from open_webui.utils.skill_files import file_summaries

        if not __user__ or __request__ is None:
            raise ValueError('Request and user context required')
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import get_terminal_skill

            if path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await get_terminal_skill(
                __request__,
                __user__,
                __metadata__ if __metadata__ is not None else {},
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                offset=offset,
                max_chars=max_chars,
                refresh=False,
            )
            if not skill:
                raise ValueError(f"Skill '{id}' not found")
            return JSONCodec.dumps(
                {'path': path, 'content': skill['content'], 'next_offset': skill['next_offset']}, ensure_ascii=False
            )
        skill = await authorized_skill(id, SimpleNamespace(**__user__))
        if not skill.is_active:
            await authorized_skill(id, SimpleNamespace(**__user__), 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        version_id = versions.get(skill.id) or skill.version_id
        snapshot = await get_skill_snapshot(skill, version_id)
        versions[skill.id] = version_id
        file = next((f for f in snapshot['data']['files'] if f['path'] == path), None)
        if file is None:
            raise ValueError('File not found')
        if file.get('encoding'):
            return JSONCodec.dumps(
                {
                    **file_summaries([file])[0],
                    'url': '/workspace/skills/edit?' + urlencode({'id': id, 'path': path}),
                }
            )
        return JSONCodec.dumps(
            {
                'path': path,
                **await skill_content_page(file['content'], offset, max_chars),
            },
            ensure_ascii=False,
        )
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def grep_skill_files(
    id: str,
    pattern: str,
    path: str | None = None,
    case_insensitive: bool = True,
    max_results: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __event_call__: callable = None,
) -> str:
    """Search skill files for text or regex matches.

    Literal text or auto-detected RE2 regex; no lookarounds/backreferences, and shorthand character classes are ASCII-only.
    Searches each line for non-empty, non-overlapping matches and returns merged, bounded excerpts (6000 characters total).
    Offsets and match_length describe the first match in each excerpt, in original-file characters; total_matches counts
    occurrences, not excerpts. Omitted excerpts have compact locations in omitted_matches; omitted_matches_truncated
    indicates when more locations remain. Terminal skills support SKILL.md only. No skill version is created.

    :param id: Skill ID, including terminal: IDs.
    :param pattern: Text or auto-detected regex to find (up to 4096 characters).
    :param path: Optional exact skill file path; otherwise search all text files, skipping binary files.
    :param case_insensitive: Ignore case when matching (default true).
    :param max_results: Maximum excerpts to return, from 1 to 30 (default 10).
    """
    from open_webui.utils.skill_files import SKILL_GREP_MAX_CHARS, grep_skill_content

    try:
        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        metadata = __metadata__ if __metadata__ is not None else {}
        if id.startswith('terminal:'):
            from open_webui.utils.terminals import load_terminal_skill

            if path is not None and path != 'SKILL.md':
                raise ValueError('Terminal skills support SKILL.md only; use terminal tools for supporting files.')
            skill = await load_terminal_skill(
                __request__,
                __user__,
                metadata,
                unquote(id.removeprefix('terminal:')),
                {'__event_call__': __event_call__},
                refresh=False,
            )
            if skill is None:
                raise ValueError(f"Skill '{id}' not found")
            files = [{'path': 'SKILL.md', 'content': skill['content']}]
        else:
            from types import SimpleNamespace

            from open_webui.models.skills import get_skill_snapshot
            from open_webui.routers.skills import authorized_skill

            actor = SimpleNamespace(**__user__)
            skill = await authorized_skill(id, actor)
            if not skill.is_active:
                await authorized_skill(id, actor, 'write')
            context = metadata.get('chat_context') or {}
            metadata['chat_context'] = context
            versions = context.setdefault('skill_versions', {})
            version_id = versions.get(skill.id) or skill.version_id
            snapshot = await get_skill_snapshot(skill, version_id)
            versions[skill.id] = version_id
            files = snapshot['data']['files']
            if path is not None:
                file = next((file for file in files if file['path'] == path), None)
                if file is None:
                    raise ValueError('File not found')
                if file.get('encoding'):
                    raise ValueError('File is not a text file')
                files = [file]
        return await asyncio.to_thread(grep_skill_content, files, pattern, case_insensitive, max_results)
    except Exception as error:
        detail = str(getattr(error, 'detail', str(error)))
        result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        while len(result) > SKILL_GREP_MAX_CHARS:
            detail = detail[: max(0, len(detail) - (len(result) - SKILL_GREP_MAX_CHARS))]
            result = JSONCodec.dumps({'error': detail}, ensure_ascii=False)
        return result


async def create_tool(
    id: str,
    name: str,
    description: str,
    content: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """Create a private Python tool in the workspace.
    Source must define a Tools class with public methods, type hints, and descriptive docstrings.
    Use Valves or UserValves for credentials; do not embed secrets. Saving executes the Python module on the server.

    :param id: Unique lowercase Python identifier for the toolkit.
    :param name: Display name.
    :param description: Short description of what the tool does.
    :param content: Complete Python source, without Markdown fences.
    """
    try:
        from types import SimpleNamespace

        from open_webui.models.tools import ToolForm
        from open_webui.routers.tools import create_new_tools
        from open_webui.utils.access_control import has_permission

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        if __user__.get('role') != 'admin' and not await has_permission(
            __user__['id'], 'workspace.tools', await Config.get('user.permissions')
        ):
            raise ValueError('Tool authoring permission required')
        result = await create_new_tools(
            __request__,
            ToolForm(id=id, name=name, content=content, meta={'description': description}, access_grants=[]),
            SimpleNamespace(**__user__),
            None,
        )
        return JSONCodec.dumps({'id': result.id, 'url': '/workspace/tools/edit?id=' + result.id})
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def create_skill(
    id: str,
    name: str,
    content: str,
    files: list[SkillFile] = None,
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """Create a private workspace skill with SKILL.md and optional UTF-8 supporting files. No terminal is needed.

    :param id: Unique lowercase skill slug.
    :param name: Display name.
    :param content: Complete SKILL.md text, including name and description frontmatter.
    :param files: Optional supporting files, each with path and content. UTF-8 text only.
    :param commit_message: Optional description of this save.
    """
    try:
        from types import SimpleNamespace

        from open_webui.models.skills import SkillForm
        from open_webui.routers.skills import create_new_skill
        from open_webui.utils.access_control import has_permission
        from open_webui.utils.skill_files import frontmatter

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        files = [f.model_dump(exclude_none=True) if isinstance(f, SkillFile) else f for f in files or []]
        if any(f.get('encoding') or f.get('path') == 'SKILL.md' for f in files):
            raise ValueError('Supporting files must be UTF-8 text; pass SKILL.md in content')
        # Agent authoring requires the workspace editor permission, not just import access.
        if __user__.get('role') != 'admin' and not await has_permission(
            __user__['id'], 'workspace.skills', await Config.get('user.permissions')
        ):
            raise ValueError('Skill authoring permission required')
        result = await create_new_skill(
            __request__,
            SkillForm(
                id=id,
                name=name,
                content=content,
                description=str(frontmatter(content).get('description', '')),
                files=[{'path': 'SKILL.md', 'content': content}, *(files or [])],
                commit_message=commit_message,
                access_grants=[],
            ),
            SimpleNamespace(**__user__),
            None,
        )
        return JSONCodec.dumps({'id': result.id, 'url': '/workspace/skills/edit?id=' + result.id})
    except Exception as error:
        return JSONCodec.dumps({'error': getattr(error, 'detail', str(error))})


async def update_skill_files(
    id: str,
    operations: list[SkillFileOperation],
    commit_message: str = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """Save file operations as a new skill version, preserving untouched files. If the skill changed since it was read, call view_skill again and reapply the edit.

    :param id: Skill to edit. Requires write access.
    :param operations: File operations: {op: put, path, content}, {op: move, path, destination}, or {op: delete, path}. Use put with path SKILL.md to edit the instructions. Text writes only.
    :param commit_message: Optional description of the change.
    """
    try:
        from types import SimpleNamespace

        from open_webui.events import EVENTS, publish_event
        from open_webui.models.skills import Skills
        from open_webui.routers.skills import authorized_skill

        if __request__ is None or not __user__:
            raise ValueError('Request and user context required')
        user = SimpleNamespace(**__user__)
        skill = await authorized_skill(id, user, 'write')
        metadata = __metadata__ if __metadata__ is not None else {}
        context = metadata.get('chat_context') or {}
        metadata['chat_context'] = context
        versions = context.setdefault('skill_versions', {})
        expected_version_id = versions.get(skill.id) or skill.version_id
        operations = [
            op.model_dump(exclude_none=True) if isinstance(op, SkillFileOperation) else op for op in operations or []
        ]
        if any(op.get('encoding') for op in operations):
            raise ValueError('Agent file writes support UTF-8 text only')
        result = await Skills.update_skill_by_id(
            id,
            {
                'expected_version_id': expected_version_id,
                'operations': operations or [],
                'commit_message': commit_message,
            },
            user_id=user.id,
        )
        versions[skill.id] = result.version_id
        await publish_event(__request__, EVENTS.SKILL_UPDATED, actor=user, subject_id=id, data={'name': result.name})
        return JSONCodec.dumps({'id': result.id})
    except Exception as error:
        detail = getattr(error, 'detail', str(error))
        if isinstance(detail, dict) and detail.get('code') == 'version_conflict':
            detail = {
                'code': 'version_conflict',
                'message': 'Skill changed. Call view_skill again and reapply the edit.',
            }
        return JSONCodec.dumps({'error': detail})


# Tool search


async def search_tools(
    query: str,
    count: int = 5,
    __metadata__: dict = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Discover available tools by capability and return their full definitions.
    Pass the exact tool name when you already know it.
    Invoke results with call_tool(calls=[{"tool_name": "exact_name", "params": "{}"}, ...]).
    Each params value is a JSON-encoded object matching that tool's parameters.
    Batch independent calls together. Do not call deferred tools directly by name.

    :param query: Keywords describing the capability you need (e.g. "jira create issue"), or an exact tool name
    :param count: Maximum number of results to return (default: 5, max: 20)
    :return: JSON with the definitions of the matching tools, which can then be invoked through call_tool
    """
    from open_webui.utils.tool_search import ToolSearchError, rank_deferred_tools

    tools = __metadata__['tools']
    candidates = {name: tools[name]['spec'] for name in __metadata__['deferred_tools']}
    try:
        matches = await rank_deferred_tools(
            query,
            candidates,
            count,
            request=__request__,
            user=UserModel(**__user__) if __user__ else None,
        )
    except ToolSearchError as error:
        return JSONCodec.dumps({'error': str(error)})
    if not matches:
        return JSONCodec.dumps(
            {'tools': [], 'message': 'No matching tools found. Try different keywords or the exact tool name.'}
        )
    return JSONCodec.dumps({'tools': [candidates[name] for name in matches]})


# Task management tools

from typing import Literal

from pydantic import BaseModel, Field

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a visible task checklist for multi-step work so progress can be shown in chat.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single visible task item as completed, in_progress, pending, or cancelled.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return JSONCodec.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return JSONCodec.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Automation tools


async def _validate_owned_automation_folder(user_id: str, folder_id: Optional[str]) -> Optional[str]:
    if not folder_id:
        return None
    from open_webui.models.folders import Folders

    folder = await Folders.get_folder_by_id_and_user_id(folder_id, user_id)
    if not folder:
        raise ValueError('Folder not found')
    return folder.id


async def create_automation(
    name: str,
    prompt: str,
    rrule: str,
    folder_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
) -> str:
    """
    Create a scheduled automation that runs a prompt on a recurring or one-time schedule.
    Use this when the user wants to schedule a task to run automatically.
    The automation will use the current chat model.

    The rrule parameter must be a valid iCalendar RRULE string. Common examples:
    - Every day at 9am: "DTSTART:20250101T090000\nRRULE:FREQ=DAILY"
    - Every Monday at 8am: "DTSTART:20250106T080000\nRRULE:FREQ=WEEKLY;BYDAY=MO"
    - Every hour: "RRULE:FREQ=HOURLY;INTERVAL=1"
    - Every 30 minutes: "RRULE:FREQ=MINUTELY;INTERVAL=30"
    - Once at a specific time: "DTSTART:20250415T140000\nRRULE:FREQ=DAILY;COUNT=1"
    - First day of every month: "DTSTART:20250101T090000\nRRULE:FREQ=MONTHLY;BYMONTHDAY=1"

    The DTSTART time should reflect the desired execution time. Use COUNT=1 for one-time automations.

    :param name: A short descriptive name for the automation
    :param prompt: The prompt/instructions to execute on each run
    :param rrule: An iCalendar RRULE string defining the schedule
    :param folder_id: Optional owner-owned folder ID for generated chats
    :return: JSON with the created automation details including id, next scheduled runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, Automations, AutomationTarget
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        # Fall back to model dict ID since __metadata__ may predate model_id assignment
        metadata = __metadata__ or {}
        model_id = metadata.get('model_id') or (
            metadata.get('model', {}).get('id') if isinstance(metadata.get('model'), dict) else None
        )
        if not model_id:
            return JSONCodec.dumps({'error': 'Could not detect current model'})

        try:
            folder_id = await _validate_owned_automation_folder(user_id, folder_id)
        except ValueError as e:
            return JSONCodec.dumps({'error': str(e)})

        # Validate the RRULE
        try:
            await validate_rrule(rrule, tz=user.timezone)
        except ValueError as e:
            return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, rrule, None, is_create=True)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=name,
            folder_id=folder_id,
            data=AutomationData(
                prompt=prompt,
                model_id=model_id,
                rrule=rrule,
                target=(
                    AutomationTarget(type='channel', channel_id=metadata.get('chat_id', '').removeprefix('channel:'))
                    if metadata.get('chat_id', '').startswith('channel:')
                    else None
                ),
            ),
            is_active=True,
        )

        automation = await Automations.insert(user_id, form, await next_run_ns(rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': automation.id,
                'name': automation.name,
                'folder_id': automation.folder_id,
                'model_id': model_id,
                'target': automation.data.get('target'),
                'is_active': automation.is_active,
                'next_runs': await next_n_runs_ns(rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_automation(
    automation_id: str,
    name: Optional[str] = None,
    prompt: Optional[str] = None,
    rrule: Optional[str] = None,
    model_id: Optional[str] = None,
    folder_id: Optional[str] = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing automation. Only the provided fields are changed; omitted fields stay the same.

    :param automation_id: The ID of the automation to update
    :param name: New name for the automation (optional)
    :param prompt: New prompt/instructions (optional)
    :param rrule: New iCalendar RRULE schedule string (optional). See create_automation for format examples.
    :param model_id: New model ID to use (optional); blank values are ignored
    :param folder_id: New owner-owned folder ID (optional); omit or pass blank to keep unchanged, pass null to clear
    :return: JSON with the updated automation details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationData, AutomationForm, Automations, AutomationTarget
        from open_webui.models.users import Users
        from open_webui.routers.automations import check_automation_limits
        from open_webui.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if not user:
            return JSONCodec.dumps({'error': 'User not found'})

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        # Merge provided fields with existing values
        new_name = name if name is not None else automation.name
        new_prompt = prompt if prompt is not None else automation.data.get('prompt', '')
        new_model_id = model_id.strip() if model_id and model_id.strip() else automation.data.get('model_id', '')
        new_rrule = rrule if rrule is not None else automation.data.get('rrule', '')
        if folder_id is None:
            new_folder_id = None
        elif not folder_id.strip():
            new_folder_id = automation.folder_id
        else:
            try:
                new_folder_id = await _validate_owned_automation_folder(user_id, folder_id.strip())
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        # Validate RRULE if changed
        if rrule is not None:
            try:
                await validate_rrule(new_rrule, tz=user.timezone)
            except ValueError as e:
                return JSONCodec.dumps({'error': f'Invalid schedule: {e}'})

        try:
            await check_automation_limits(__request__, user, new_rrule, None)
        except HTTPException as e:
            return JSONCodec.dumps({'error': e.detail})

        tz = user.timezone
        form = AutomationForm(
            name=new_name,
            folder_id=new_folder_id,
            data=AutomationData(
                prompt=new_prompt,
                model_id=new_model_id,
                rrule=new_rrule,
                target=AutomationTarget(**automation.data['target']) if automation.data.get('target') else None,
            ),
            is_active=automation.is_active,
        )

        updated = await Automations.update_by_id(automation_id, form, await next_run_ns(new_rrule, tz=tz))

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': updated.id,
                'name': updated.name,
                'folder_id': updated.folder_id,
                'model_id': new_model_id,
                'target': updated.data.get('target'),
                'is_active': updated.is_active,
                'next_runs': await next_n_runs_ns(new_rrule, tz=tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_automations(
    status: Optional[str] = None,
    folder_id: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's scheduled automations.

    :param status: Filter by status: "active", "paused", or omit for all
    :param folder_id: Optional owner-owned folder ID filter; pass an empty string to clear the folder filter
    :param count: Maximum number of automations to return (default: 10)
    :return: JSON list of automations with id, name, prompt snippet, schedule, status, and next runs
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_n_runs_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)
        if folder_id:
            try:
                folder_id = await _validate_owned_automation_folder(user_id, folder_id)
            except ValueError as e:
                return JSONCodec.dumps({'error': str(e)})

        result = await Automations.search_automations(
            user_id=user_id,
            status=status,
            folder_id=folder_id,
            skip=0,
            limit=count,
        )

        automations = []
        for item in result.items:
            rrule = item.data.get('rrule', '')
            prompt_text = item.data.get('prompt', '')
            snippet = prompt_text[:100] + ('...' if len(prompt_text) > 100 else '')

            automations.append(
                {
                    'id': item.id,
                    'name': item.name,
                    'folder_id': item.folder_id,
                    'prompt_snippet': snippet,
                    'model_id': item.data.get('model_id', ''),
                    'target': item.data.get('target'),
                    'rrule': rrule,
                    'is_active': item.is_active,
                    'last_run_at': item.last_run_at,
                    'next_runs': await next_n_runs_ns(rrule, tz=user.timezone if user else None),
                }
            )

        return JSONCodec.dumps(
            {'automations': automations, 'total': result.total},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'list_automations error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def toggle_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Pause or resume a scheduled automation. If active, it will be paused. If paused, it will be resumed.

    :param automation_id: The ID of the automation to toggle
    :return: JSON with the updated automation status
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import Automations
        from open_webui.models.users import Users
        from open_webui.utils.automations import next_run_ns

        user_id = __user__.get('id')
        user = await Users.get_user_by_id(user_id)

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        rrule = automation.data.get('rrule', '')
        toggled = await Automations.toggle(
            automation_id,
            await next_run_ns(rrule, tz=user.timezone if user else None),
        )

        return JSONCodec.dumps(
            {
                'status': 'success',
                'id': toggled.id,
                'name': toggled.name,
                'is_active': toggled.is_active,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'toggle_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_automation(
    automation_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a scheduled automation and all its run history.

    :param automation_id: The ID of the automation to delete
    :return: JSON confirming the automation was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.automations import AutomationRuns, Automations

        user_id = __user__.get('id')

        automation = await Automations.get_by_id(automation_id)
        if not automation:
            return JSONCodec.dumps({'error': 'Automation not found'})
        if automation.user_id != user_id:
            return JSONCodec.dumps({'error': 'Access denied'})

        name = automation.name
        await AutomationRuns.delete_by_automation(automation_id)
        await Automations.delete(automation_id)

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Automation "{name}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_automation error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# Calendar tools


MAX_CALENDAR_RANGE_END_NS = 2**63 - 1


def _get_user_tz(user_dict: dict):
    """Get the user's timezone as a ZoneInfo, falling back to UTC."""
    from zoneinfo import ZoneInfo

    tz_name = None
    if user_dict:
        tz_name = user_dict.get('timezone')
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo('UTC')


def _dt_to_ns(dt_str: str, tz) -> int:
    """Convert a datetime string to nanoseconds since epoch, interpreting in the given timezone."""
    from datetime import datetime

    dt = datetime.fromisoformat(dt_str)
    # If naive (no timezone info), localize to user's timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1_000) * 1_000_000


def _ns_to_dt(ns: int, tz) -> str:
    """Convert nanoseconds since epoch to a datetime string in the given timezone."""
    from datetime import datetime

    seconds = ns / 1_000_000_000
    dt = datetime.fromtimestamp(seconds, tz=tz)
    return dt.strftime('%Y-%m-%d %H:%M')


def _event_to_dict(event, tz) -> dict:
    """Convert a calendar event model to a human-friendly dict with local timestamps."""
    alert_minutes = None
    if event.meta and 'alert_minutes' in event.meta:
        alert_minutes = event.meta['alert_minutes']
    return {
        'id': event.id,
        'calendar_id': event.calendar_id,
        'title': event.title,
        'description': event.description or '',
        'start': _ns_to_dt(event.start_at, tz),
        'end': _ns_to_dt(event.end_at, tz) if event.end_at else None,
        'all_day': event.all_day,
        'location': event.location or '',
        'reminder_minutes': alert_minutes if alert_minutes is not None else 10,
        'color': event.color,
        'is_cancelled': event.is_cancelled,
    }


async def search_calendar_events(
    query: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search calendar events, reminders, and scheduled items by text and/or date range.
    Helpful for finding upcoming events, reminders, or schedule items.

    :param query: Search text to match against event title, description, or location (optional)
    :param start: Only return events starting at or after this datetime, e.g. "2026-04-20 00:00" (optional)
    :param end: Only return events starting before this datetime, e.g. "2026-04-27 00:00" (optional)
    :param count: Maximum number of events to return (default: 10)
    :return: JSON list of matching events with id, title, description, start, end, calendar_id, location
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEvents

        user_id = __user__.get('id')
        tz = _get_user_tz(__user__)

        if isinstance(count, str):
            try:
                count = int(count)
            except ValueError:
                count = 10

        if start or end:
            # Date range query — use get_events_by_range
            try:
                start_ns = _dt_to_ns(start, tz) if start else 0
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

            try:
                end_ns = _dt_to_ns(end, tz) if end else MAX_CALENDAR_RANGE_END_NS
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

            items = await CalendarEvents.get_events_by_range(
                user_id=user_id,
                start=start_ns,
                end=end_ns,
            )

            # Apply text filter if query is also provided
            if query:
                q = query.lower()
                items = [
                    e
                    for e in items
                    if q in (e.title or '').lower()
                    or q in (e.description or '').lower()
                    or q in (e.location or '').lower()
                ]

            events = [_event_to_dict(item, tz) for item in items[:count]]
            return JSONCodec.dumps(
                {'events': events, 'total': len(items)},
                ensure_ascii=False,
            )
        else:
            # Text-only search
            result = await CalendarEvents.search_events(
                user_id=user_id,
                query=query,
                skip=0,
                limit=count,
            )

            events = [_event_to_dict(item, tz) for item in result.items]
            return JSONCodec.dumps(
                {'events': events, 'total': result.total},
                ensure_ascii=False,
            )
    except Exception as e:
        log.exception(f'search_calendar_events error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def create_calendar_event(
    title: str,
    start: str,
    end: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: Optional[str] = None,
    all_day: bool = False,
    location: Optional[str] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a calendar event, reminder, or alarm. Use this when the user wants to
    schedule an event, set a reminder, create an alarm, or says things like
    "remind me", "don't let me forget", "notify me at", or "add to my calendar".
    For simple reminders, omit end/location/all_day and set reminder_minutes to 0.

    :param title: Event or reminder title (e.g. "Team standup", "Take medicine", "Call mom")
    :param start: Start datetime in the user's local time (e.g. "2026-04-20 09:00")
    :param end: End datetime in the user's local time (optional — omit for reminders or point-in-time events)
    :param description: Event description or notes (optional)
    :param calendar_id: Target calendar ID (optional, uses default calendar if omitted)
    :param all_day: Whether this is an all-day event (default: false)
    :param location: Event location (optional)
    :param reminder_minutes: Minutes before the event to send a notification (optional, default: 10). Use 0 for "at time of event", -1 for no notification.
    :return: JSON with the created event details including id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEventForm, CalendarEvents, Calendars

        user_id = __user__.get('id')

        # Resolve calendar_id: use provided, or fall back to default
        if not calendar_id:
            calendars = await Calendars.get_calendars_by_user(user_id)
            default_cal = next((c for c in calendars if c.is_default), None)
            if not default_cal and calendars:
                default_cal = calendars[0]
            if not default_cal:
                return JSONCodec.dumps({'error': 'No calendars found. Cannot create event.'})
            calendar_id = default_cal.id

        # Verify access
        cal = await Calendars.get_calendar_by_id(calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Calendar not found'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            from open_webui.models.access_grants import AccessGrants
            from open_webui.models.groups import Groups

            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied to this calendar'})

        # Coerce boolean from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        try:
            start_ns = _dt_to_ns(start, tz)
        except (ValueError, TypeError) as e:
            return JSONCodec.dumps({'error': f'Invalid start datetime: {e}. Use format like "2026-04-20 09:00"'})

        end_ns = None
        if end:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}. Use format like "2026-04-20 10:00"'})
        elif not all_day:
            # Default to 1 hour duration
            end_ns = start_ns + 3_600_000_000_000

        # Build meta with reminder setting
        meta = {}
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = 10
            meta['alert_minutes'] = reminder_minutes
        else:
            meta['alert_minutes'] = 10

        form = CalendarEventForm(
            calendar_id=calendar_id,
            title=title,
            description=description,
            start_at=start_ns,
            end_at=end_ns,
            all_day=all_day,
            location=location,
            meta=meta,
        )

        event = await CalendarEvents.insert_new_event(user_id, form)
        if not event:
            return JSONCodec.dumps({'error': 'Failed to create event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(event, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_calendar_event(
    event_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_day: Optional[bool] = None,
    location: Optional[str] = None,
    is_cancelled: Optional[bool] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing calendar event. Only provided fields are changed;
    omitted fields stay the same.

    :param event_id: The ID of the event to update
    :param title: New event title (optional)
    :param description: New event description (optional)
    :param start: New start datetime string in your local time, e.g. "2026-04-20 09:00" (optional)
    :param end: New end datetime string in your local time (optional)
    :param all_day: Whether this is an all-day event (optional)
    :param location: New event location (optional)
    :param is_cancelled: Set to true to cancel the event (optional)
    :param reminder_minutes: Minutes before the event to send a reminder notification (optional). Use 0 for "at time of event", -1 for no reminder. Accepts any positive integer for custom timing (e.g. 120 for 2 hours before).
    :return: JSON with the updated event details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, CalendarEventUpdateForm, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access to the event's calendar
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        # Coerce boolean strings from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')
        if isinstance(is_cancelled, str):
            is_cancelled = is_cancelled.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        start_ns = None
        if start is not None:
            try:
                start_ns = _dt_to_ns(start, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

        end_ns = None
        if end is not None:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

        # Build meta update with reminder setting if provided
        meta = None
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = None
            if reminder_minutes is not None:
                meta = {'alert_minutes': reminder_minutes}

        update_fields = {
            'title': title,
            'description': description,
            'start_at': start_ns,
            'end_at': end_ns,
            'all_day': all_day,
            'location': location,
            'is_cancelled': is_cancelled,
            'meta': meta,
        }
        form = CalendarEventUpdateForm(**{k: v for k, v in update_fields.items() if v is not None})

        updated = await CalendarEvents.update_event_by_id(event_id, form)
        if not updated:
            return JSONCodec.dumps({'error': 'Failed to update event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(updated, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_calendar_event(
    event_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a calendar event permanently.

    :param event_id: The ID of the event to delete
    :return: JSON confirming the event was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        title = event.title
        result = await CalendarEvents.delete_event_by_id(event_id)
        if not result:
            return JSONCodec.dumps({'error': 'Failed to delete event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Event "{title}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})
