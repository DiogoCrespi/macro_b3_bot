"""Parser for structured CVM capital-composition exports."""
from __future__ import annotations

from datetime import datetime, timezone
import csv
import hashlib
import io
from pathlib import Path
import zipfile
from typing import Any, Iterable


def _parse_number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _timestamp(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class CVMCapitalCompositionReader:
    """Select the latest disclosed version available at an assessment cut."""

    section_name = "Dados da Empresa/Composição do Capital"

    def read(
        self,
        path: Path,
        *,
        cvm_code: str,
        assessment_as_of: datetime,
    ) -> list[dict[str, Any]]:
        source_payload = path.read_bytes()
        source_checksum = hashlib.sha256(source_payload).hexdigest()
        files: list[tuple[str, bytes]] = []
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(source_payload)) as archive:
                files = [
                    (name, archive.read(name)) for name in archive.namelist()
                    if name.lower().endswith((".csv", ".txt"))
                ]
        else:
            files = [(path.name, source_payload)]
        candidates: list[dict[str, Any]] = []
        for name, payload in files:
            candidates.extend(self._rows(
                payload, name=name, cvm_code=cvm_code,
                assessment_as_of=assessment_as_of,
                source_checksum=source_checksum,
            ))
        # A replay must never combine multiple versions for the same basis.
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for row in candidates:
            key = (row["capital_reference_date"].isoformat(), row["share_class"])
            previous = selected.get(key)
            if previous is None or (
                row["document_available_at"], row["document_version"]
            ) > (previous["document_available_at"], previous["document_version"]):
                selected[key] = row
        return list(selected.values())

    def _rows(
        self,
        payload: bytes,
        *,
        name: str,
        cvm_code: str,
        assessment_as_of: datetime,
        source_checksum: str,
    ) -> list[dict[str, Any]]:
        text = payload.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: list[dict[str, Any]] = []
        for raw in reader:
            normalized = {
                str(key).strip().upper(): value for key, value in raw.items() if key
            }
            company = str(
                normalized.get("CD_CVM") or normalized.get("CVM_CODE") or ""
            ).strip()
            if company and company != str(cvm_code):
                continue
            available = _timestamp(
                normalized.get("DT_RECEB") or normalized.get("DOCUMENT_AVAILABLE_AT")
                or normalized.get("DATA_RECEBIMENTO")
            )
            if available is None or available > assessment_as_of:
                continue
            reference = _timestamp(
                normalized.get("DT_REFER") or normalized.get("CAPITAL_REFERENCE_DATE")
            )
            if reference is None:
                continue
            description = str(
                normalized.get("DS_CONTA") or normalized.get("DESCRIPTION")
                or normalized.get("SHARE_CLASS") or ""
            ).upper()
            share_class = str(
                normalized.get("SHARE_CLASS") or normalized.get("TP_ACAO")
                or normalized.get("CLASS") or description
            ).strip()
            emitted = _parse_number(
                normalized.get("SHARES_ISSUED") or normalized.get("QT_ACOES")
                or normalized.get("EMITTED_COUNT")
            )
            treasury = _parse_number(
                normalized.get("TREASURY_SHARES") or normalized.get("ACOES_TESOURARIA")
            ) or 0.0
            outstanding = _parse_number(
                normalized.get("OUTSTANDING_COUNT") or normalized.get("SHARES_OUTSTANDING")
            )
            if outstanding is None and emitted is not None:
                outstanding = emitted - treasury
            if outstanding is None or outstanding <= 0:
                continue
            row_bytes = "|".join(str(value or "") for value in raw.values()).encode()
            rows.append({
                "cvm_code": str(cvm_code),
                "share_class": share_class,
                "share_count": emitted,
                "treasury_shares": treasury,
                "outstanding_count": outstanding,
                "capital_reference_date": reference,
                "document_available_at": available,
                "document_version": str(normalized.get("VERSAO") or normalized.get("VERSION") or "0"),
                "document_id": str(normalized.get("DOCUMENT_ID") or normalized.get("ID_DOCUMENTO") or name),
                "document_checksum": source_checksum,
                "source_file": name,
                "source_row_hash": hashlib.sha256(row_bytes).hexdigest(),
                "section": self.section_name,
            })
        return rows

