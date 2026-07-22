from __future__ import annotations

import asyncio
import httpx
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timezone
from .normalizer import compute_raw_checksum

class BcbHttpClient:
    """Cliente HTTP assíncrono genérico com suporte a retries, cache bruto e timeout."""
    def __init__(self, raw_cache_dir: Path | None = None, timeout_seconds: float = 15.0, retries: int = 3):
        self.raw_cache_dir = raw_cache_dir
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.headers = {"User-Agent": "MacroB3Bot/1.0 (BCB Data Pipeline)"}

    async def get_json(self, url: str) -> tuple[Any, str, bytes]:
        """
        Executa requisicao GET, armazena no cache bruto e retorna (json_payload, sha256_checksum, raw_bytes).
        """
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=self.headers) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    raw_bytes = resp.content
                    checksum = compute_raw_checksum(raw_bytes)
                    
                    if self.raw_cache_dir:
                        self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
                        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        cache_file = self.raw_cache_dir / f"{timestamp}_{checksum[:12]}.json"
                        cache_file.write_bytes(raw_bytes)
                        
                    return resp.json(), checksum, raw_bytes
            except Exception as e:
                last_error = e
                if attempt < self.retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                else:
                    raise RuntimeError(f"Erro na requisicao HTTP para {url} apos {self.retries} tentativas: {last_error}")
