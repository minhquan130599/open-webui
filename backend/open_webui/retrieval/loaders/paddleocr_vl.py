import base64
import logging
import os
import sys
from typing import List

import requests
from langchain_core.documents import Document
from open_webui.env import GLOBAL_LOG_LEVEL

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)

PADDLEOCR_VL_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp']
PADDLEOCR_VL_SUPPORTED_EXTENSIONS = ['pdf'] + PADDLEOCR_VL_IMAGE_EXTENSIONS
# Seconds to wait for the OCR result of one file (long PDFs take minutes).
PADDLEOCR_VL_READ_TIMEOUT = int(os.environ.get('PADDLEOCR_VL_TIMEOUT', '900'))


class PaddleOCRVLLoader:
    """Loader that uses PaddleOCR-vl API to extract text from PDF/images."""

    def __init__(
        self,
        api_url: str,
        token: str,
        file_path: str,
        auth_scheme: str = 'token',
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

    def load(self) -> List[Document]:
        log.info('Processing with PaddleOCR-vl: %s', self.file_path)

        try:
            with open(self.file_path, 'rb') as file:
                file_bytes = file.read()
                file_data = base64.b64encode(file_bytes).decode('ascii')
        except Exception as e:
            log.error(f'Failed to read file {self.file_path}: {e}')
            raise

        headers = {'Authorization': f'{self.auth_scheme} {self.token}', 'Content-Type': 'application/json'}

        # Detect fileType based on file extension
        ext = self.file_path.lower().split('.')[-1]
        file_type = 1 if ext in PADDLEOCR_VL_IMAGE_EXTENSIONS else 0

        payload = {
            'file': file_data,
            'fileType': file_type,
            'useDocOrientationClassify': False,
            'useDocUnwarping': False,
            'useChartRecognition': False,
        }

        url = f'{self.api_url}/layout-parsing'
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=(30, PADDLEOCR_VL_READ_TIMEOUT))
        except requests.Timeout:
            raise RuntimeError(
                f'PaddleOCR-VL không trả lời sau {PADDLEOCR_VL_READ_TIMEOUT} giây ({url}). '
                'File quá lớn hoặc máy OCR quá tải; proxy RunPod tự ngắt sau khoảng 100 giây.'
            )
        except requests.RequestException as e:
            raise RuntimeError(f'Không kết nối được PaddleOCR-VL ({url}): {e}')

        if response.status_code >= 400:
            hint = {
                401: 'sai token, hoặc máy OCR cần kiểu xác thực khác (token/Bearer)',
                403: 'sai token, hoặc máy OCR cần kiểu xác thực khác (token/Bearer)',
                404: 'sai đường dẫn: kiểm tra API Base URL (thường không có /v1)',
                413: 'file quá lớn với máy OCR hoặc proxy',
                502: 'máy OCR đang tắt hoặc lỗi',
                503: 'máy OCR đang tắt hoặc quá tải',
                504: 'máy OCR trả lời quá lâu',
                524: 'proxy RunPod ngắt vì xử lý quá 100 giây (file quá lớn)',
            }.get(response.status_code, 'máy OCR báo lỗi')
            body = ' '.join((response.text or '').split())[:200]
            raise RuntimeError(f'PaddleOCR-VL lỗi HTTP {response.status_code} ({hint}) tại {url}: {body}')

        try:
            result = response.json().get('result') or {}
            layout_results = result.get('layoutParsingResults') or []
        except Exception:
            body = ' '.join((response.text or '').split())[:200]
            raise RuntimeError(f'PaddleOCR-VL trả về dữ liệu không đúng định dạng ({url}): {body}')

        documents = []
        total_pages = len(layout_results)
        skipped_pages = 0

        for i, res in enumerate(layout_results):
            markdown_text = (res.get('markdown') or {}).get('text', '')

            if isinstance(markdown_text, str):
                cleaned_content = markdown_text.strip()
            else:
                cleaned_content = str(markdown_text).strip()

            if not cleaned_content:
                skipped_pages += 1
                continue

            documents.append(
                Document(
                    page_content=cleaned_content,
                    metadata={
                        'page': i,
                        'page_label': i + 1,
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
