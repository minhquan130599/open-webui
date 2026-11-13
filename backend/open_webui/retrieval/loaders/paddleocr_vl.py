import base64
import io
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

import requests
from langchain_core.documents import Document
from open_webui.env import GLOBAL_LOG_LEVEL

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)

PADDLEOCR_VL_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp']
PADDLEOCR_VL_SUPPORTED_EXTENSIONS = ['pdf'] + PADDLEOCR_VL_IMAGE_EXTENSIONS
# Seconds to wait for the OCR result of one request (one batch of pages).
PADDLEOCR_VL_READ_TIMEOUT = int(os.environ.get('PADDLEOCR_VL_TIMEOUT', '900'))
# Statuses worth a retry: the OCR server or the proxy in front of it was busy, restarting or too slow.
RETRY_STATUSES = {429, 502, 503, 504, 524}
RETRIES = 2

HTTP_HINTS = {
    401: 'sai token, hoặc máy OCR cần kiểu xác thực khác (token/Bearer)',
    403: 'sai token, hoặc máy OCR cần kiểu xác thực khác (token/Bearer)',
    404: 'sai đường dẫn: kiểm tra API Base URL (thường không có /v1)',
    413: 'gói gửi quá lớn: giảm số trang mỗi lần gửi',
    429: 'máy OCR đang quá tải',
    502: 'máy OCR đang tắt hoặc lỗi',
    503: 'máy OCR đang tắt hoặc quá tải',
    504: 'máy OCR trả lời quá lâu: giảm số trang mỗi lần gửi',
    524: 'proxy RunPod ngắt vì xử lý quá 100 giây: giảm số trang mỗi lần gửi',
}


class _Retryable(RuntimeError):
    pass


class PaddleOCRVLLoader:
    """Loader that uses PaddleOCR-vl API to extract text from PDF/images.

    Long PDFs are cut into batches of `pages_per_request` pages, sent `concurrency` at a time, so each request stays
    small and short (proxies such as RunPod's cut a request after about 100 seconds)."""

    def __init__(
        self,
        api_url: str,
        token: str,
        file_path: str,
        auth_scheme: str = 'token',
        pages_per_request: int = 10,
        concurrency: int = 2,
    ):
        if not api_url or not token:
            raise ValueError('PaddleOCR-vl API URL and Token are required.')
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found at {file_path}')

        self.api_url = api_url.rstrip('/')
        self.token = token
        # 'bearer' -> Authorization: Bearer <token>; anything else -> Authorization: token <token>
        self.auth_scheme = 'Bearer' if (auth_scheme or '').strip().lower() == 'bearer' else 'token'
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        # 0 (or less) sends the whole PDF in one request.
        self.pages_per_request = max(int(pages_per_request or 0), 0)
        self.concurrency = min(max(int(concurrency or 1), 1), 8)

    def load(self) -> List[Document]:
        log.info('Processing with PaddleOCR-vl: %s', self.file_path)

        try:
            with open(self.file_path, 'rb') as file:
                file_bytes = file.read()
        except Exception as e:
            log.error(f'Failed to read file {self.file_path}: {e}')
            raise

        ext = self.file_path.lower().split('.')[-1]
        file_type = 1 if ext in PADDLEOCR_VL_IMAGE_EXTENSIONS else 0

        batches = self._split(file_bytes) if file_type == 0 else None
        if batches:
            log.info(
                'PaddleOCR-vl: %s split into %s requests of up to %s pages (%s at a time)',
                self.file_name,
                len(batches),
                self.pages_per_request,
                self.concurrency,
            )
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                results = list(
                    pool.map(lambda b: (b[0], self._request(b[2], file_type, f'trang {b[0] + 1}–{b[1]}')), batches)
                )
            total_pages = batches[-1][1]
        else:
            pages = self._request(file_bytes, file_type, '')
            results = [(0, pages)]
            total_pages = len(pages)

        documents = []
        skipped_pages = 0
        for first, pages in results:
            for j, markdown_text in enumerate(pages):
                cleaned_content = (markdown_text if isinstance(markdown_text, str) else str(markdown_text)).strip()
                if not cleaned_content:
                    skipped_pages += 1
                    continue
                documents.append(
                    Document(
                        page_content=cleaned_content,
                        metadata={
                            'page': first + j,
                            'page_label': first + j + 1,
                            'total_pages': total_pages,
                            'file_name': self.file_name,
                            'processing_engine': 'paddleocr-vl',
                        },
                    )
                )

        if skipped_pages > 0:
            log.info('PaddleOCR-vl: Processed %s pages, skipped %s empty pages.', len(documents), skipped_pages)

        if not documents:
            log.warning('No valid text content found by PaddleOCR-vl.')
            if file_type == 0:
                # A PDF without any text is a failed extraction: fail the file instead of embedding a placeholder.
                raise ValueError(f'PaddleOCR-VL không đọc được chữ nào trong file ({total_pages} trang).')
            return [
                Document(
                    page_content='No valid text content found in document',
                    metadata={
                        'error': 'no_valid_pages',
                        'file_name': self.file_name,
                        'processing_engine': 'paddleocr-vl',
                    },
                )
            ]

        return documents

    def _split(self, file_bytes: bytes):
        """[(first page index, end page number, pdf bytes)] when the PDF is longer than one batch, else None."""
        if self.pages_per_request <= 0:
            return None
        try:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(io.BytesIO(file_bytes))
            if reader.is_encrypted:
                reader.decrypt('')
            count = len(reader.pages)
            if count <= self.pages_per_request:
                return None
            batches = []
            for first in range(0, count, self.pages_per_request):
                end = min(first + self.pages_per_request, count)
                writer = PdfWriter()
                for i in range(first, end):
                    writer.add_page(reader.pages[i])
                out = io.BytesIO()
                writer.write(out)
                batches.append((first, end, out.getvalue()))
            return batches
        except Exception as e:
            # Unreadable for pypdf (damaged, protected...): let the OCR server try the whole file.
            log.warning('PaddleOCR-vl: cannot split %s (%s), sending it whole', self.file_name, e)
            return None

    def _request(self, data: bytes, file_type: int, label: str) -> list:
        """Markdown text of every page of one request, retrying when the server is temporarily unavailable."""
        for attempt in range(RETRIES + 1):
            try:
                return self._post(data, file_type, label)
            except _Retryable as e:
                if attempt == RETRIES:
                    raise RuntimeError(str(e))
                log.warning('PaddleOCR-vl: %s, retrying (%s/%s)', e, attempt + 1, RETRIES)
                time.sleep(3 * (attempt + 1))

    def _post(self, data: bytes, file_type: int, label: str) -> list:
        url = f'{self.api_url}/layout-parsing'
        where = f' ({label})' if label else ''
        payload = {
            'file': base64.b64encode(data).decode('ascii'),
            'fileType': file_type,
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        }
        headers = {'Authorization': f'{self.auth_scheme} {self.token}', 'Content-Type': 'application/json'}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=(30, PADDLEOCR_VL_READ_TIMEOUT))
        except requests.Timeout:
            raise _Retryable(
                f'PaddleOCR-VL không trả lời sau {PADDLEOCR_VL_READ_TIMEOUT} giây{where} ({url}). '
                'Máy OCR quá tải hoặc gửi quá nhiều trang một lần.'
            )
        except requests.ConnectionError as e:
            raise _Retryable(f'Không kết nối được PaddleOCR-VL{where} ({url}): {e}')
        except requests.RequestException as e:
            raise RuntimeError(f'Không kết nối được PaddleOCR-VL{where} ({url}): {e}')

        if response.status_code >= 400:
            hint = HTTP_HINTS.get(response.status_code, 'máy OCR báo lỗi')
            body = ' '.join((response.text or '').split())[:200]
            message = f'PaddleOCR-VL lỗi HTTP {response.status_code} ({hint}){where} tại {url}: {body}'
            raise (_Retryable if response.status_code in RETRY_STATUSES else RuntimeError)(message)

        try:
            result = response.json().get('result') or {}
            layout_results = result.get('layoutParsingResults') or []
        except Exception:
            body = ' '.join((response.text or '').split())[:200]
            raise RuntimeError(f'PaddleOCR-VL trả về dữ liệu không đúng định dạng{where} ({url}): {body}')
        return [(res.get('markdown') or {}).get('text', '') for res in layout_results]
