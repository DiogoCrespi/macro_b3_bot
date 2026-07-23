"""Targeted official CVM ingestion for the 15-company exposure pilot."""
from __future__ import annotations

import yaml

from macro_b3_bot.application.ingest_cvm import CvmIngestionPipeline
from macro_b3_bot.application.reconcile_company_mappings import _MAPPING_PATH
from macro_b3_bot.config import Settings


async def ingest_company_pilot() -> dict[str, object]:
    with open(_MAPPING_PATH, encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    cvm_codes = {str(item["cvm_code"]) for item in config["mappings"]}
    pipeline = CvmIngestionPipeline(Settings())
    itr = await pipeline.ingest_statements("ITR", [2026], cvm_codes)
    dfp = await pipeline.ingest_statements("DFP", [2025], cvm_codes)
    return {
        "companies": len(cvm_codes),
        "itr_documents": itr.documents_received,
        "itr_lines": itr.lines_received,
        "dfp_documents": dfp.documents_received,
        "dfp_lines": dfp.lines_received,
        "inserted_lines": itr.lines_inserted + dfp.lines_inserted,
        "duplicate_lines": itr.lines_duplicated + dfp.lines_duplicated,
    }
