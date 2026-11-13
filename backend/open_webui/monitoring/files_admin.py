"""Knowledge files for the monitor page: the original file, and re-processing one file with the current engine.

Re-processing does what "Reindex" does, for one file: extract the text again (current extraction engine), embed
it into the file's own collection, then into every knowledge base that contains the file (old chunks of that file
removed first).
"""

import asyncio
import logging
import time

log = logging.getLogger(__name__)


async def original_path(file_id: str) -> tuple[str, str, str]:
    """(local path, file name, content type) of the original upload."""
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    file = await Files.get_file_by_id(file_id)
    if not file or not file.path:
        raise FileNotFoundError(file_id)
    path = await asyncio.to_thread(Storage.get_file, file.path)
    return path, file.filename, (file.meta or {}).get('content_type') or 'application/octet-stream'


async def reprocess(app, file_id: str) -> dict:
    from open_webui.internal.db import get_async_db
    from open_webui.models.files import Files
    from open_webui.models.knowledge import Knowledges
    from open_webui.models.users import Users
    from open_webui.monitoring.reindex import internal_request
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
    from open_webui.routers.retrieval import ProcessFileForm, index_file, process_file

    file = await Files.get_file_by_id(file_id)
    if not file:
        raise FileNotFoundError(file_id)
    admin = await Users.get_super_admin_user()
    request = internal_request(app)
    started = time.perf_counter()
    knowledge_ids = [
        k.id
        for k in await Knowledges.get_knowledge_bases()
        if file_id in {f.id for f in await Knowledges.get_files_by_id(k.id)}
    ]
    collection = f'file-{file_id}'
    if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=collection):
        await ASYNC_VECTOR_DB_CLIENT.delete_collection(collection_name=collection)
    # Old chunks go first, so a failed extraction does not leave stale (or error-message) chunks searchable.
    for knowledge_id in knowledge_ids:
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file_id})
        except Exception:
            pass
    try:
        async with get_async_db() as db:
            await index_file(request, ProcessFileForm(file_id=file_id), admin, db)
    except Exception:
        # index_file already marked the file failed with the reason; drop the old text as well.
        async with get_async_db() as db:
            await Files.update_file_data_by_id(file_id, {'content': ''}, db=db)
        raise
    async with get_async_db() as db:
        await Files.update_file_data_by_id(file_id, {'status': 'completed', 'error': None}, db=db)
    for knowledge_id in knowledge_ids:
        async with get_async_db() as db:
            await process_file(
                request, ProcessFileForm(file_id=file_id, collection_name=knowledge_id), user=admin, db=db
            )
    file = await Files.get_file_by_id(file_id)
    content = (file.data or {}).get('content') or ''
    log.info('Monitor: re-processed file %s (%s) in %d knowledge bases', file.filename, file_id, len(knowledge_ids))
    return {
        'file_id': file_id,
        'knowledge_bases': len(knowledge_ids),
        'chars': len(content),
        'ms': round((time.perf_counter() - started) * 1000),
    }
