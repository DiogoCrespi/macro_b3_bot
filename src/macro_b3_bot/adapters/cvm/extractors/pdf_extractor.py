from __future__ import annotations

import io
from typing import Tuple
from .base import BaseExtractor, normalize_document_text

class PdfExtractor(BaseExtractor):
    """
    Extrator de texto para documentos PDF da CVM via pypdf / pypdfium2 com fallback gracioso.
    """
    def extract_text(self, content_bytes: bytes) -> Tuple[str, int, float]:
        pages_text = []
        page_count = 0

        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content_bytes))
            page_count = len(reader.pages)
            for page in reader.pages:
                txt = page.extract_text() or ""
                pages_text.append(txt)
        except Exception:
            # Fallback basico de extracao crua se pypdf nao puder ler
            page_count = 1
            pages_text.append(content_bytes.decode("latin-1", errors="ignore"))

        full_raw = "\n\n".join(pages_text)
        norm_text = normalize_document_text(full_raw)
        
        # Qualidade: se < 100 caracteres, requer OCR
        quality = 0.85 if len(norm_text) >= 100 else 0.20
        return norm_text, max(1, page_count), quality
