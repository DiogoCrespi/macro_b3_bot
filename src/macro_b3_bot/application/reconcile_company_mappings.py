"""Validate the 15-company pilot mapping against the ingested CVM registry."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from macro_b3_bot.infrastructure.store import DatabaseStore

_MAPPING_PATH = Path(__file__).resolve().parents[3] / "config" / "company_ticker_cvm_pilot.yaml"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


class PilotMappingReconciler:
    def __init__(self, store: DatabaseStore, path: Path = _MAPPING_PATH) -> None:
        self.store = store
        self.path = path

    def reconcile(self) -> dict[str, object]:
        with open(self.path, encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        valid_from = date.fromisoformat(config["valid_from"])
        failures: list[dict[str, str]] = []
        inserted: list[str] = []
        for item in config["mappings"]:
            registry = self.store.connection.execute(
                """
                SELECT cvm_code,cnpj,legal_name,collected_at
                FROM cvm_companies
                WHERE cvm_code = ?
                ORDER BY collected_at DESC LIMIT 1
                """,
                [item["cvm_code"]],
            ).fetchone()
            if not registry:
                failures.append({"ticker": item["ticker"], "reason": "CVM_CODE_NOT_IN_REGISTRY"})
                continue
            if _digits(str(registry[1])) != _digits(item["cnpj"]):
                failures.append({"ticker": item["ticker"], "reason": "CNPJ_MISMATCH"})
                continue
            if str(registry[2]).strip().upper() != item["legal_name"].strip().upper():
                failures.append({"ticker": item["ticker"], "reason": "LEGAL_NAME_MISMATCH"})
                continue
            evidence_id = f"CVM_REGISTRY_{item['cvm_code']}_{_digits(item['cnpj'])}"
            self.store.save_ticker_mapping({
                **item,
                "mapping_source": config["source"],
                "confidence": 1.0,
                "validated": True,
                "valid_from": valid_from,
                "valid_to": None,
                "review_status": config["review_status"],
                "evidence_id": evidence_id,
                "mapping_version": config["mapping_version"],
                "created_at": registry[3] or datetime.now(timezone.utc),
            })
            inserted.append(item["ticker"])
        return {
            "requested": len(config["mappings"]),
            "validated": len(inserted),
            "tickers": inserted,
            "failures": failures,
            "mapping_version": config["mapping_version"],
        }
