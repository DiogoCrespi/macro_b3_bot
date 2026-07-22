from __future__ import annotations

import re
from typing import Tuple
from .base import BaseExtractor, normalize_document_text

class HtmlExtractor(BaseExtractor):
    """
    Extrator de texto para documentos HTML da CVM.
    Remove scripts, estilos, navegação e tags HTML preservando conteúdo textual e tabelas.
    """
    def extract_text(self, content_bytes: bytes) -> Tuple[str, int, float]:
        try:
            raw_text = content_bytes.decode("utf-8", errors="ignore")
        except Exception:
            raw_text = content_bytes.decode("iso-8859-1", errors="ignore")

        # Remove scripts e estilos
        cleaned = re.sub(r"<script[^>]*?>.*?</script>", "", raw_text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<style[^>]*?>.*?</style>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        
        # Converte quebras de linha de tags br, p, tr
        cleaned = re.sub(r"<(br|p|tr|div)[^>]*?>", "\n", cleaned, flags=re.IGNORECASE)
        # Strip demais tags HTML
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)

        norm_text = normalize_document_text(cleaned)
        quality = 0.90 if len(norm_text) > 100 else 0.40
        return norm_text, 1, quality
