from __future__ import annotations

import json
from pathlib import Path
import duckdb
from pydantic import BaseModel


class AuditStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.connection.execute(
            """
            create table if not exists audit_events (
                run_id varchar,
                entity_type varchar,
                entity_id varchar,
                payload_json varchar,
                inserted_at timestamp default current_timestamp
            )
            """
        )

    def append(self, run_id: str, entity_type: str, entity_id: str, payload: BaseModel | dict) -> None:
        raw = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        self.connection.execute(
            "insert into audit_events(run_id, entity_type, entity_id, payload_json) values (?, ?, ?, ?)",
            [run_id, entity_type, entity_id, json.dumps(raw, ensure_ascii=False)],
        )

    def close(self) -> None:
        self.connection.close()
