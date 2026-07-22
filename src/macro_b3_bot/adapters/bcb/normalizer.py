from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

def parse_decimal(raw_val: Any) -> Decimal:
    """Converte valores com vírgula decimal ou inteiros/floats para Decimal com segurança."""
    if raw_val is None:
        raise ValueError("Valor nulo nao pode ser convertido para Decimal")
    
    val_str = str(raw_val).strip().replace(",", ".")
    try:
        dec = Decimal(val_str)
        if dec.is_nan() or dec.is_infinite():
            raise ValueError(f"Valor nao numerico ou infinito: {raw_val}")
        return dec
    except InvalidOperation:
        raise ValueError(f"Formato decimal invalido: {raw_val}")

def compute_raw_checksum(data: bytes | str) -> str:
    """Gera hash SHA-256 para auditoria de integridade do payload bruto."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def split_date_range(start: date, end: date, max_years: int = 5) -> list[tuple[date, date]]:
    """
    Divide automaticamente um intervalo extenso de datas em blocos menores (max 5 anos)
    para respeitar a janela de limite do API do BCB SGS.
    """
    if start > end:
        raise ValueError(f"Data inicial {start} nao pode ser maior que final {end}")

    ranges = []
    current_start = start

    while current_start <= end:
        # Adiciona max_years - 1 dia para nao estourar a janela
        target_year = current_start.year + max_years
        try:
            current_end = date(target_year, current_start.month, current_start.day) - timedelta(days=1)
        except ValueError: # Tratamento para 29 de fevereiro em ano bissexto
            current_end = date(target_year, current_start.month, 28) - timedelta(days=1)

        if current_end > end:
            current_end = end

        ranges.append((current_start, current_end))
        current_start = current_end + timedelta(days=1)

    return ranges
