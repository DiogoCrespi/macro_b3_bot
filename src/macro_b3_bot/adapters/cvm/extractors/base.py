from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Tuple

def normalize_document_text(text: str) -> str:
    """
    Normaliza o texto extraído (NFKC Unicode, remoção de espaços em branco duplicados e quebras excessivas)
    preservando números, sinais, moedas e porcentagens.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

class BaseExtractor(ABC):
    """
    Classe abstrata base para extratores de texto de documentos (HTML, PDF, TXT).
    """
    @abstractmethod
    def extract_text(self, content_bytes: bytes) -> Tuple[str, int, float]:
        """
        Retorna (extracted_text, page_count, quality_score).
        """
        pass
