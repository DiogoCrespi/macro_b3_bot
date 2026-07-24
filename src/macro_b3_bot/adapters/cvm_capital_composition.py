"""Parser for structured CVM capital-composition exports."""
from __future__ import annotations

from datetime import datetime, timezone
import csv
import hashlib
import io
from pathlib import Path
import zipfile
from typing import Any


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
        cnpj: str | None = None,
        document_available_at: datetime | None = None,
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
                cnpj=cnpj,
                assessment_as_of=assessment_as_of,
                document_available_at=document_available_at,
                source_checksum=source_checksum,
            ))
        # A replay must never combine multiple periods or versions for one
        # class: select the latest capital reference date available at the cut.
        selected: dict[str, dict[str, Any]] = {}
        for row in candidates:
            key = row["share_class"]
            previous = selected.get(key)
            if previous is None or (
                row["capital_reference_date"], row["document_available_at"],
                row["document_version"]
            ) > (
                previous["capital_reference_date"], previous["document_available_at"],
                previous["document_version"]
            ):
                selected[key] = row
        return list(selected.values())

    def _rows(
        self,
        payload: bytes,
        *,
        name: str,
        cvm_code: str,
        cnpj: str | None,
        assessment_as_of: datetime,
        document_available_at: datetime | None,
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
            company = str(normalized.get("CD_CVM") or normalized.get("CVM_CODE") or "").strip()
            company_cnpj = "".join(ch for ch in str(
                normalized.get("CNPJ_CIA") or normalized.get("CNPJ_COMPANHIA") or ""
            ) if ch.isdigit())
            expected_cnpj = "".join(ch for ch in str(cnpj or "") if ch.isdigit())
            if company:
                if company != str(cvm_code):
                    continue
            elif not expected_cnpj or not company_cnpj or company_cnpj != expected_cnpj:
                continue
            available = _timestamp(
                normalized.get("DT_RECEB") or normalized.get("DOCUMENT_AVAILABLE_AT")
                or normalized.get("DATA_RECEBIMENTO")
            ) or document_available_at
            if available is None or available > assessment_as_of:
                continue
            reference = _timestamp(
                normalized.get("DT_REFER") or normalized.get("CAPITAL_REFERENCE_DATE")
            )
            if reference is None:
                continue
            if reference > assessment_as_of:
                continue
            share_class = str(
                normalized.get("SHARE_CLASS") or normalized.get("TP_ACAO")
                or normalized.get("CLASS") or "TOTAL"
            ).strip()
            if not share_class:
                continue
            version_raw = normalized.get("VERSAO") or normalized.get("VERSION")
            try:
                document_version = int(str(version_raw or "").strip())
            except ValueError:
                continue
            emitted = _parse_number(
                normalized.get("SHARES_ISSUED") or normalized.get("QT_ACOES")
                or normalized.get("EMITTED_COUNT")
                or normalized.get("QT_ACAO_TOTAL_CAP_INTEGR")
                or normalized.get("QUANTIDADE_TOTAL_ACOES")
            )
            treasury = _parse_number(
                normalized.get("TREASURY_SHARES") or normalized.get("ACOES_TESOURARIA")
                or normalized.get("QT_ACAO_TOTAL_TESOURO")
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
                "company_cnpj": company_cnpj or expected_cnpj,
                "share_class": share_class,
                "share_count": emitted,
                "treasury_shares": treasury,
                "outstanding_count": outstanding,
                "capital_reference_date": reference,
                "document_available_at": available,
                "document_version": document_version,
                "document_id": str(normalized.get("DOCUMENT_ID") or normalized.get("ID_DOCUMENTO") or name),
                "document_checksum": source_checksum,
                "source_file": name,
                "source_row_hash": hashlib.sha256(row_bytes).hexdigest(),
                "section": self.section_name,
            })
        return rows
