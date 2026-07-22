from __future__ import annotations

from typing import Tuple
from .base import BaseExtractor, normalize_document_text

class TextExtractor(BaseExtractor):
    """
    Extrator de texto para arquivos de texto puro (.txt).
    """
    def extract_text(self, content_bytes: bytes) -> Tuple[str, int, float]:
        try:
            raw_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = content_bytes.decode("iso-8859-1", errors="ignore")

        norm_text = normalize_document_text(raw_text)
        quality = 1.0 if len(norm_text) > 0 else 0.0
        return norm_text, 1, quality
