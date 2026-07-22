from __future__ import annotations

from pathlib import Path
from typing import Any
import httpx


class MiroFishClient:
    """Thin configurable adapter around the MiroFish HTTP service.

    Endpoint details can change between releases; all prefixes are injected by config.
    """

    def __init__(
        self,
        base_url: str,
        *,
        graph_prefix: str = "/api/graph",
        simulation_prefix: str = "/api/simulation",
        report_prefix: str = "/api/report",
        timeout_seconds: float = 120,
    ):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self.graph_prefix = graph_prefix.rstrip("/")
        self.simulation_prefix = simulation_prefix.rstrip("/")
        self.report_prefix = report_prefix.rstrip("/")

    def healthcheck(self) -> bool:
        for path in ("/", "/health", f"{self.graph_prefix}/project/list"):
            try:
                response = self.client.get(path)
                if response.status_code < 500:
                    return True
            except httpx.HTTPError:
                continue
        return False

    def generate_ontology(
        self,
        seed_files: list[Path],
        simulation_requirement: str,
        *,
        project_name: str,
        additional_context: str = "",
    ) -> dict[str, Any]:
        opened = []
        try:
            multipart = []
            for path in seed_files:
                handle = path.open("rb")
                opened.append(handle)
                multipart.append(("files", (path.name, handle, "application/octet-stream")))
            response = self.client.post(
                f"{self.graph_prefix}/ontology/generate",
                files=multipart,
                data={
                    "simulation_requirement": simulation_requirement,
                    "project_name": project_name,
                    "additional_context": additional_context,
                },
            )
            response.raise_for_status()
            return response.json()
        finally:
            for handle in opened:
                handle.close()

    def create_simulation(self, project_id: str, graph_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"project_id": project_id, "enable_twitter": True, "enable_reddit": True}
        if graph_id:
            payload["graph_id"] = graph_id
        response = self.client.post(f"{self.simulation_prefix}/create", json=payload)
        response.raise_for_status()
        return response.json()

    def list_reports(self, simulation_id: str) -> dict[str, Any]:
        response = self.client.get(f"{self.report_prefix}/list", params={"simulation_id": simulation_id})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()
