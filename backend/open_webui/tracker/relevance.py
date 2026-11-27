"""How close each work item is to the new messages, by meaning, with the embedding model of Knowledge (bge-m3).

Used to choose the items shown to the update model when the list does not fit (schema._items_for_prompt): a
message that says "PA05 HN" or "báo cáo T11" finds the item it is about even when it shares no exact word with it
(abbreviations, synonyms, another word order). Item vectors are cached by their text, so only new or changed items
are embedded. When no embedding model is available (the tracker running as its own worker, the engine failing),
None is returned and the shared-word count is used instead.
"""

import hashlib
import logging
import sys
from collections import OrderedDict
from typing import Optional

log = logging.getLogger(__name__)

CACHE_SIZE = 20_000
MESSAGE_CHARS = 2_000  # of each message embedded; the start says what it is about
MAX_MESSAGES = 40

_cache: 'OrderedDict[str, list[float]]' = OrderedDict()


def _embedding_function():
    """The app's embedding function, when the tracker runs inside Open WebUI (TRACKER_WORKER_MODE=app)."""
    main = sys.modules.get('open_webui.main')
    app = getattr(main, 'app', None)
    return getattr(getattr(app, 'state', None), 'EMBEDDING_FUNCTION', None)


def _normalized(vectors):
    import numpy as np

    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or not array.size or not np.isfinite(array).all():
        raise ValueError('invalid embedding vectors')
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


async def closest(queries: list[str], candidates: list[str]) -> Optional[list[tuple[int, float]]]:
    """For each query, the candidate closest in meaning and how close (cosine), or None without embeddings."""
    if not queries or not candidates:
        return None
    scores = await _matrix(candidates, queries)
    if scores is None:
        return None
    best = scores.argmax(axis=0)
    return [(int(best[q]), float(scores[best[q], q])) for q in range(len(queries))]


async def _matrix(item_texts: list[str], message_texts: list[str]):
    """items x messages cosine matrix (numpy), or None."""
    function = _embedding_function()
    messages = [text[:MESSAGE_CHARS] for text in message_texts]
    if function is None or not item_texts or not messages:
        return None
    try:
        from open_webui.models.config import Config

        prefixes = await Config.get_many('rag.embedding_query_prefix', 'rag.embedding_content_prefix')
        query_prefix = prefixes.get('rag.embedding_query_prefix')
        content_prefix = prefixes.get('rag.embedding_content_prefix')
        asked = _normalized(await function(messages, prefix=query_prefix))
        keys = [hashlib.sha256(f'{content_prefix}\n{text}'.encode()).hexdigest() for text in item_texts]
        missing = {key: text for key, text in zip(keys, item_texts) if key not in _cache}
        if missing:
            vectors = await function(list(missing.values()), prefix=content_prefix)
            if len(vectors) != len(missing):
                raise ValueError('embedding count does not match')
            for key, vector in zip(missing, vectors):
                _cache[key] = list(vector)
                while len(_cache) > CACHE_SIZE:
                    _cache.popitem(last=False)
        for key in keys:
            _cache.move_to_end(key)
        items = _normalized([_cache[key] for key in keys])
        if items.shape[1] != asked.shape[1]:
            raise ValueError('embedding sizes differ (model changed)')
        return items @ asked.T
    except Exception as e:
        log.warning('Tracker: embedding failed, matching by shared words instead: %s', e)
        return None


async def item_scores(item_texts: list[str], message_texts: list[str]) -> Optional[list[float]]:
    """For each item, its highest similarity to any of the messages (a batch can be about several things)."""
    messages = [text for text in message_texts if text and text.strip()][-MAX_MESSAGES:]
    scores = await _matrix(item_texts, messages)
    return None if scores is None else scores.max(axis=1).tolist()
