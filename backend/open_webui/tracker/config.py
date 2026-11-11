import os


def _bool_env(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() == 'true'


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name) or default), minimum)
    except (ValueError, TypeError):
        return default


ENABLE_PROJECT_TRACKER = _bool_env('ENABLE_PROJECT_TRACKER', False)

# 'app' runs the worker loop inside every Open WebUI process; 'external' expects
# `python -m open_webui.tracker.worker` to run in its own container.
TRACKER_WORKER_MODE = os.getenv('TRACKER_WORKER_MODE', 'app').strip().lower()

# OpenAI-compatible endpoint (vLLM) used for state updates. When unset, the tracker uses the first enabled
# OpenAI connection from Admin > Settings > Connections, read on every call so edits there apply without
# a restart (OPENAI_API_BASE_URL only seeds that setting on first start).
TRACKER_LLM_BASE_URL = (os.getenv('TRACKER_LLM_BASE_URL') or '').rstrip('/')
TRACKER_LLM_API_KEY = os.getenv('TRACKER_LLM_API_KEY') or ''
TRACKER_MODEL = os.getenv('TRACKER_MODEL', '')
TRACKER_LLM_TIMEOUT = _int_env('TRACKER_LLM_TIMEOUT', 300)
TRACKER_MAX_TOKENS = _int_env('TRACKER_MAX_TOKENS', 4096)
# vLLM structured output (response_format json_schema). Falls back to plain JSON if rejected.
TRACKER_GUIDED_JSON = _bool_env('TRACKER_GUIDED_JSON', True)
# Sends chat_template_kwargs.enable_thinking=false (Qwen3 templates); ignored by other templates.
TRACKER_DISABLE_THINKING = _bool_env('TRACKER_DISABLE_THINKING', True)

# Cluster-wide cap on concurrent tracker LLM calls (counted via active leases).
TRACKER_MAX_CONCURRENCY = _int_env('TRACKER_MAX_CONCURRENCY', 2)
TRACKER_POLL_INTERVAL = _int_env('TRACKER_POLL_INTERVAL', 5)
# Long enough for a batch with several file summaries (each file may take a few LLM calls).
TRACKER_LEASE_SECONDS = _int_env('TRACKER_LEASE_SECONDS', 900)

# A channel is processed once it has this many pending messages, or after it has been idle this long.
TRACKER_BATCH_SIZE = _int_env('TRACKER_BATCH_SIZE', 30)
TRACKER_IDLE_SECONDS = _int_env('TRACKER_IDLE_SECONDS', 90)
TRACKER_BATCH_MAX_MESSAGES = _int_env('TRACKER_BATCH_MAX_MESSAGES', 80)
# Sized for a 32k-token context: state (~4k tokens) + batch (~10k tokens) + output (4k tokens).
TRACKER_BATCH_MAX_CHARS = _int_env('TRACKER_BATCH_MAX_CHARS', 24_000)
TRACKER_MESSAGE_MAX_CHARS = _int_env('TRACKER_MESSAGE_MAX_CHARS', 2_000)
TRACKER_STATE_MAX_CHARS = _int_env('TRACKER_STATE_MAX_CHARS', 12_000)

# Unprocessed messages injected verbatim when answering, so answers include the latest activity.
TRACKER_TAIL_LIMIT = _int_env('TRACKER_TAIL_LIMIT', 50)
TRACKER_TAIL_MAX_CHARS = _int_env('TRACKER_TAIL_MAX_CHARS', 8_000)

TRACKER_TIMEZONE = os.getenv('TRACKER_TIMEZONE', 'Asia/Ho_Chi_Minh')

# Attachments (P2). Files use the text Open WebUI already extracted; images go to the same model as vision input.
TRACKER_ENABLE_FILES = _bool_env('TRACKER_ENABLE_FILES', True)
TRACKER_ENABLE_IMAGES = _bool_env('TRACKER_ENABLE_IMAGES', True)
TRACKER_FILE_CHUNK_CHARS = _int_env('TRACKER_FILE_CHUNK_CHARS', 12_000)
# Longer files are summarized from evenly spaced chunks, and the summary says so.
TRACKER_FILE_MAX_CHUNKS = _int_env('TRACKER_FILE_MAX_CHUNKS', 8)
TRACKER_ATTACHMENT_SUMMARY_MAX_CHARS = _int_env('TRACKER_ATTACHMENT_SUMMARY_MAX_CHARS', 1_500)
# A message waits this long for Open WebUI to finish extracting its files before it is processed without them.
TRACKER_FILE_WAIT_SECONDS = _int_env('TRACKER_FILE_WAIT_SECONDS', 600)
TRACKER_IMAGE_MAX_SIDE = _int_env('TRACKER_IMAGE_MAX_SIDE', 1024)
TRACKER_MAX_ATTACHMENTS_PER_MESSAGE = _int_env('TRACKER_MAX_ATTACHMENTS_PER_MESSAGE', 6)
