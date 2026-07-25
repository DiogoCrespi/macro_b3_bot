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
        """Validates MiroFish HTTP service health.

        Requires HTTP 200 OK from a known endpoint and a valid JSON object.
        Rejects 401, 403, 404, 429, or 5xx responses.
        """
        for path in (f"{self.graph_prefix}/project/list", "/health"):
            try:
                response = self.client.get(path)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, (dict, list)):
                        return True
            except (httpx.HTTPError, ValueError):
                continue
        return False

    def generate_ontology(
        self,
        seed_files: list[Path | str],
        simulation_requirement: str,
        *,
        project_name: str,
        additional_context: str = "",
    ) -> dict[str, Any]:
        opened = []
        try:
            multipart = []
            for item in seed_files:
                path = Path(item)
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

    def create_simulation(
        self,
        project_id: str,
        graph_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project_id": project_id,
            "enable_twitter": True,
            "enable_reddit": True,
        }
        if graph_id:
            payload["graph_id"] = graph_id
        if config:
            payload.update(config)
        response = self.client.post(f"{self.simulation_prefix}/create", json=payload)
        response.raise_for_status()
        return response.json()

    def list_reports(
        self,
        project_id: str | None = None,
        simulation_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if project_id:
            params["project_id"] = project_id
        if simulation_id:
            params["simulation_id"] = simulation_id
        response = self.client.get(f"{self.report_prefix}/list", params=params)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()

