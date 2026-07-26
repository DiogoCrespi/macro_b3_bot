from __future__ import annotations

from pathlib import Path
import time
import os
from typing import Any
from hashlib import sha256
import json
import httpx


MIROFISH_REPORT_SCHEMA_VERSION = "5A.3-mirofish-scenario-report-v1"
MIROFISH_REPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MacroB3 MiroFish Scenario Report",
    "type": "object",
    "required": ["schema_version", "report_text", "scenarios"],
    "additionalProperties": True,
    "properties": {
        "schema_version": {"const": MIROFISH_REPORT_SCHEMA_VERSION},
        "report_text": {"type": "string", "minLength": 1},
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["scenario_type", "trigger", "report_excerpt"],
                "properties": {
                    "scenario_type": {"type": "string"},
                    "trigger": {"type": "string", "minLength": 1},
                    "actors": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "string"}},
                    "macro_factors": {"type": "array", "items": {"type": "string"}},
                    "sector_effects": {"type": "array", "items": {"type": "string"}},
                    "second_order_effects": {"type": "array", "items": {"type": "string"}},
                    "expected_horizon": {"type": "string"},
                    "report_excerpt": {"type": "string", "minLength": 1},
                    "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}


def mirofish_report_schema_hash() -> str:
    canonical = json.dumps(MIROFISH_REPORT_SCHEMA, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


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
        # Local Ollama-backed ontology/report generation can legitimately take
        # several minutes on CPU. Keep the HTTP request alive long enough for
        # the real sidecar workflow; semantic parsing never falls back locally.
        timeout_seconds: float = 600,
    ):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_failure_threshold = 3
        self._circuit_cooldown_seconds = 30.0
        self.graph_prefix = graph_prefix.rstrip("/")
        self.simulation_prefix = simulation_prefix.rstrip("/")
        self.report_prefix = report_prefix.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Perform an HTTP request with a fail-closed circuit breaker."""
        if time.monotonic() < self._circuit_open_until:
            raise RuntimeError("MIROFISH_CIRCUIT_OPEN")
        try:
            response = self.client.request(method, path, **kwargs)
            if response.status_code >= 500:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0
            if self._consecutive_failures >= self._circuit_failure_threshold:
                self._circuit_open_until = time.monotonic() + self._circuit_cooldown_seconds
            return response
        except httpx.HTTPError:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._circuit_failure_threshold:
                self._circuit_open_until = time.monotonic() + self._circuit_cooldown_seconds
            raise

    def healthcheck(self) -> bool:
        """Validates MiroFish HTTP service health.

        Requires HTTP 200 OK from a known endpoint and a valid JSON object.
        Rejects 401, 403, 404, 429, or 5xx responses.
        """
        for path in (f"{self.graph_prefix}/project/list", "/health"):
            try:
                response = self._request("GET", path)
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
            response = self._request("POST",
                f"{self.graph_prefix}/ontology/generate",
                files=multipart,
                data={
                    "simulation_requirement": simulation_requirement,
                    "project_name": project_name,
                    "additional_context": additional_context,
                },
            )
            response.raise_for_status()
            res = response.json()
            if isinstance(res, dict) and isinstance(res.get("data"), dict):
                return res["data"]
            return res
        finally:
            for handle in opened:
                handle.close()

    def poll_project_ontology(
        self,
        project_id: str,
        *,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Polls GET /api/graph/project/<project_id> until graph build completes."""
        start_time = time.monotonic()
        attempts = 0
        last_status = "UNKNOWN"
        while (time.monotonic() - start_time) < timeout_seconds:
            attempts += 1
            try:
                response = self._request("GET", f"{self.graph_prefix}/project/{project_id}")
                if response.status_code == 200:
                    res = response.json()
                    data = res.get("data", res) if isinstance(res, dict) else res
                    if isinstance(data, dict):
                        status = str(data.get("status") or "SUCCESS")
                        status_normalized = status.lower()
                        last_status = status
                        if status_normalized in ("ontology_generated", "graph_built", "graph_completed", "completed", "success", "created"):
                            return data
                        if status_normalized in ("failed", "error", "failed_graph_build"):
                            raise RuntimeError(f"Project graph build failed with status {status}")
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(interval_seconds)
        return {"project_id": project_id, "last_status": last_status, "attempts": attempts}

    def build_graph(self, project_id: str, *, graph_name: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"project_id": project_id}
        if graph_name:
            payload["graph_name"] = graph_name
        response = self._request("POST", f"{self.graph_prefix}/build", json=payload)
        response.raise_for_status()
        res = response.json()
        return res.get("data", res) if isinstance(res, dict) else res

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
        response = self._request("POST", f"{self.simulation_prefix}/create", json=payload)
        response.raise_for_status()
        res = response.json()
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            return res["data"]
        return res

    @staticmethod
    def structured_report_config() -> dict[str, Any]:
        """Return the explicit sidecar contract for native structured reports.

        The schema is sent to MiroFish as part of the simulation request.  The
        engine never synthesizes a report when the sidecar does not honor it.
        """
        return {
            "report_format": "json",
            "report_schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
            "report_schema_hash": mirofish_report_schema_hash(),
            "report_schema": MIROFISH_REPORT_SCHEMA,
            "require_structured_report": True,
        }

    @staticmethod
    def validate_structured_report(report: Any) -> tuple[bool, str]:
        """Validate the minimum native report contract without coercion."""
        if not isinstance(report, dict):
            return False, "REPORT_NOT_OBJECT"
        if report.get("schema_version") != MIROFISH_REPORT_SCHEMA_VERSION:
            return False, "REPORT_SCHEMA_VERSION_MISMATCH"
        scenarios = report.get("scenarios")
        if not isinstance(report.get("report_text"), str) or not report["report_text"].strip():
            return False, "REPORT_TEXT_MISSING"
        if not isinstance(scenarios, list):
            return False, "SCENARIOS_NOT_ARRAY"
        list_fields = ("actors", "actions", "macro_factors", "sector_effects", "second_order_effects")
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                return False, f"SCENARIO_{index}_NOT_OBJECT"
            if not str(scenario.get("trigger", "")).strip():
                return False, f"SCENARIO_{index}_MISSING_TRIGGER"
            if not str(scenario.get("report_excerpt", "")).strip():
                return False, f"SCENARIO_{index}_MISSING_REPORT_EXCERPT"
            for field in list_fields:
                if field in scenario and not isinstance(scenario[field], list):
                    return False, f"SCENARIO_{index}_{field.upper()}_NOT_ARRAY"
                if field in scenario and any(not isinstance(item, str) for item in scenario[field]):
                    return False, f"SCENARIO_{index}_{field.upper()}_ITEM_NOT_STRING"
            confidence = scenario.get("confidence")
            if confidence is not None and (not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
                return False, f"SCENARIO_{index}_INVALID_CONFIDENCE"
        return True, "VALID"

    def poll_simulation(
        self,
        simulation_id: str,
        *,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Polls simulation status until runner completes or returns status."""
        start_time = time.monotonic()
        attempts = 0
        last_status = "UNKNOWN"
        while (time.monotonic() - start_time) < timeout_seconds:
            attempts += 1
            try:
                response = self._request("GET", f"{self.simulation_prefix}/{simulation_id}")
                if response.status_code == 200:
                    res = response.json()
                    data = res.get("data", res) if isinstance(res, dict) else res
                    if isinstance(data, dict):
                        status = str(data.get("status") or "created")
                        last_status = status
                        if status in ("COMPLETED", "FINISHED", "SUCCESS", "completed", "finished"):
                            return data
                        if status in ("FAILED", "ERROR"):
                            raise RuntimeError(f"Simulation failed with status {status}")
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(interval_seconds)
        return {"simulation_id": simulation_id, "last_status": last_status, "attempts": attempts}

    def prepare_simulation(self, simulation_id: str, *, use_llm_for_profiles: bool = True) -> dict[str, Any]:
        response = self._request("POST",
            f"{self.simulation_prefix}/prepare",
            json={"simulation_id": simulation_id, "use_llm_for_profiles": use_llm_for_profiles},
        )
        response.raise_for_status()
        res = response.json()
        return res.get("data", res) if isinstance(res, dict) else res

    def poll_prepare(
        self,
        simulation_id: str,
        task_id: str | None = None,
        *,
        timeout_seconds: float = 600.0,
        interval_seconds: float = 2.0,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout_seconds:
            response = self._request("POST",
                f"{self.simulation_prefix}/prepare/status",
                json={"simulation_id": simulation_id, **({"task_id": task_id} if task_id else {})},
            )
            response.raise_for_status()
            res = response.json()
            data = res.get("data", res) if isinstance(res, dict) else res
            if isinstance(data, dict) and str(data.get("status", "")).lower() in {"ready", "completed", "success", "failed", "error"}:
                return data
            time.sleep(interval_seconds)
        return {"simulation_id": simulation_id, "status": "TIMEOUT_PREPARE"}

    def start_simulation(self, simulation_id: str, *, max_rounds: int = 5) -> dict[str, Any]:
        response = self._request("POST",
            f"{self.simulation_prefix}/start",
            json={"simulation_id": simulation_id, "platform": "parallel", "max_rounds": max_rounds},
        )
        response.raise_for_status()
        res = response.json()
        return res.get("data", res) if isinstance(res, dict) else res

    def stop_simulation(self, simulation_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of a running sidecar simulation."""
        if not simulation_id:
            raise ValueError("simulation_id is required")
        response = self._request(
            "POST",
            f"{self.simulation_prefix}/stop",
            json={"simulation_id": simulation_id},
        )
        response.raise_for_status()
        res = response.json()
        return res.get("data", res) if isinstance(res, dict) else res

    def poll_run_status(
        self,
        simulation_id: str,
        *,
        timeout_seconds: float = 900.0,
        interval_seconds: float = 5.0,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout_seconds:
            response = self._request("GET", f"{self.simulation_prefix}/{simulation_id}/run-status")
            response.raise_for_status()
            res = response.json()
            data = res.get("data", res) if isinstance(res, dict) else res
            if isinstance(data, dict):
                status = str(data.get("runner_status", data.get("status", ""))).lower()
                if status in {"completed", "finished", "success", "failed", "error", "stopped"}:
                    return data
            time.sleep(interval_seconds)
        return {"simulation_id": simulation_id, "runner_status": "TIMEOUT_RUN"}

    def generate_report(self, simulation_id: str) -> dict[str, Any]:
        response = self._request("POST", f"{self.report_prefix}/generate", json={"simulation_id": simulation_id})
        response.raise_for_status()
        res = response.json()
        return res.get("data", res) if isinstance(res, dict) else res

    def poll_generate_report(
        self,
        simulation_id: str,
        task_id: str | None = None,
        *,
        timeout_seconds: float = 900.0,
        interval_seconds: float = 5.0,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < timeout_seconds:
            try:
                response = self._request("POST",
                    f"{self.report_prefix}/generate/status",
                    json={"simulation_id": simulation_id, **({"task_id": task_id} if task_id else {})},
                )
                response.raise_for_status()
                res = response.json()
            except httpx.HTTPStatusError:
                # The sidecar can finish writing the report at the same instant
                # that its progress-file status endpoint observes an incomplete
                # JSON write and returns 500.  Recover only from a persisted
                # completed report; never synthesize a report or task status.
                try:
                    reports = self.list_reports(simulation_id=simulation_id).get("reports", [])
                except (httpx.HTTPError, ValueError, TypeError):
                    reports = []
                completed = [
                    item for item in reports
                    if isinstance(item, dict)
                    and str(item.get("status", "")).lower() in {"completed", "success"}
                ]
                if completed:
                    latest = completed[-1]
                    return {
                        "simulation_id": simulation_id,
                        "report_id": latest.get("report_id"),
                        "status": "completed",
                        "recovered_from_status_endpoint_error": True,
                    }
                time.sleep(interval_seconds)
                continue
            data = res.get("data", res) if isinstance(res, dict) else res
            if isinstance(data, dict) and str(data.get("status", "")).lower() in {"completed", "success", "failed", "error"}:
                return data
            time.sleep(interval_seconds)
        return {"simulation_id": simulation_id, "status": "TIMEOUT_REPORT"}

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
        response = self._request("GET", f"{self.report_prefix}/list", params=params)
        response.raise_for_status()
        res = response.json()
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            return res["data"]
        if isinstance(res, dict) and isinstance(res.get("data"), list):
            return {"reports": res["data"], "count": len(res["data"])}
        if isinstance(res, list):
            return {"reports": res, "count": len(res)}
        return res

    def extract_structured_report(self, report_text: str) -> dict[str, Any]:
        """Extract the strict scenario schema from narrative sidecar output.

        This is a separate, auditable extraction step using the configured
        Ollama/OpenAI-compatible backend. It never supplies local scenarios or
        confidence values; malformed output is returned for strict validation.
        """
        if not isinstance(report_text, str) or not report_text.strip():
            raise ValueError("REPORT_TEXT_MISSING_FOR_EXTRACTION")
        llm_url = os.getenv("MIROFISH_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        model = os.getenv("MIROFISH_LLM_MODEL", os.getenv("LLM_MODEL_NAME", "qwen2.5:7b"))
        prompt = (
            "Extract scenarios from the MiroFish report below. Return ONLY valid JSON "
            f"matching schema version {MIROFISH_REPORT_SCHEMA_VERSION}. Preserve report_excerpt "
            "as an exact substring of report_text. The JSON MUST contain a non-empty "
            "scenarios array (at least one item) with scenario_type, trigger, actors, "
            "actions, macro_factors, sector_effects, second_order_effects, "
            "expected_horizon and report_excerpt. actors/actions/macro_factors/"
            "sector_effects/second_order_effects MUST be JSON arrays of strings "
            "(use [] when the report has no items). Derive every field only from the "
            "report; sector_effects MUST explicitly name the affected sector when "
            "the report names it (for example retail/varejo). "
            "report; do not invent facts, actors, effects, or confidence. The trigger "
            "field MUST be a string, never an array. Every scenario object MUST have "
            "its own report_excerpt field; a top-level excerpt is invalid. Use null "
            "when confidence is not explicitly stated. report_excerpt is an evidence "
            "anchor: copy a contiguous substring character-for-character from the "
            "report, preserving the original Unicode language, punctuation and "
            "whitespace; never translate, summarize or paraphrase it.\n\n"
            + report_text
        )
        current_prompt = prompt
        last_parsed: dict[str, Any] | None = None
        last_reason = "UNKNOWN"
        # Local models occasionally return object-valued sector_effects even
        # after the first repair.  Give the strict contract a few bounded
        # attempts; malformed output still fails closed and never gets
        # coerced into a hypothesis.
        for attempt in range(4):
            response = httpx.post(
                f"{llm_url}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": current_prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=600,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("STRUCTURED_EXTRACTION_NOT_OBJECT")
            if "scenarios" not in parsed and {
                "scenario_type", "trigger", "report_excerpt"
            }.issubset(parsed):
                parsed = {
                    "scenarios": [parsed],
                    "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
                    "_normalization_applied": "SINGLE_SCENARIO_OBJECT_TO_ARRAY",
                }
            elif "scenarios" not in parsed:
                parsed["_normalization_applied"] = "NONE"
            parsed["schema_version"] = MIROFISH_REPORT_SCHEMA_VERSION
            parsed["_extraction_metadata"] = {
                "extraction_model": model,
                "extraction_prompt": current_prompt,
                "extraction_prompt_hash": sha256(current_prompt.encode("utf-8")).hexdigest(),
                "extraction_schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
                "raw_extraction_response": content,
                "extraction_response_checksum": sha256(content.encode("utf-8")).hexdigest(),
                "extraction_mode": "LLM_STRUCTURED_EXTRACTION_FROM_MIROFISH_REPORT",
                "extraction_attempt": attempt + 1,
            }
            last_parsed = parsed
            valid, reason = MiroFishClient.validate_structured_report(
                {**parsed, "report_text": report_text}
            )
            if valid:
                return parsed
            last_reason = reason
            current_prompt = (
                prompt
                + "\n\nREPAIR REQUIRED: previous JSON failed strict validation with "
                + reason
                + ". Return corrected JSON. Each scenario MUST include a string "
                + "trigger and a report_excerpt copied exactly from report_text; "
                + "do not put report_excerpt only at the top level. Every item in "
                + "actors, actions, macro_factors, sector_effects and "
                + "second_order_effects MUST be a plain JSON string, never an object "
                + "or nested array. For sector_effects write strings such as "
                + "'VAREJO: custos maiores pressionam margens'."
            )
        if last_parsed is not None:
            last_parsed["_extraction_metadata"]["final_validation_error"] = last_reason
            return last_parsed
        raise ValueError("STRUCTURED_EXTRACTION_NO_RESPONSE")

    def poll_report(
        self,
        simulation_id: str,
        project_id: str | None = None,
        *,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Poll until the sidecar exposes a *completed* report.

        ``/api/report/list`` exposes a report row as soon as planning starts.
        Returning that row caused the engine to persist incomplete reports and
        then (correctly) reject them as unsupported.  A report is usable only
        when its status is terminal and it contains the generated markdown (or
        the section endpoint reports completion).
        """
        start_time = time.monotonic()
        attempts = 0
        while (time.monotonic() - start_time) < timeout_seconds:
            attempts += 1
            try:
                res = self.list_reports(project_id=project_id, simulation_id=simulation_id)
                reports = res.get("reports", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                completed = []
                for report in reports:
                    if not isinstance(report, dict):
                        continue
                    status = str(report.get("status", "")).lower()
                    if status not in {"completed", "success", "finished"}:
                        continue
                    if str(report.get("markdown_content", "")).strip():
                        completed.append(report)
                        continue
                    report_id = report.get("report_id")
                    if not report_id:
                        continue
                    try:
                        sections_response = self._request(
                            "GET", f"{self.report_prefix}/{report_id}/sections"
                        )
                        sections_payload = sections_response.json()
                        sections_data = (
                            sections_payload.get("data", sections_payload)
                            if isinstance(sections_payload, dict)
                            else {}
                        )
                        sections = sections_data.get("sections", []) if isinstance(sections_data, dict) else []
                        if sections_data.get("is_complete") and any(
                            isinstance(section, dict) and str(section.get("content", "")).strip()
                            for section in sections
                        ):
                            merged = dict(report)
                            merged["markdown_content"] = "\n\n".join(
                                str(section.get("content", ""))
                                for section in sections
                                if isinstance(section, dict)
                            )
                            completed.append(merged)
                    except (httpx.HTTPError, ValueError, TypeError):
                        continue
                if completed:
                    return {"reports": completed, "attempts": attempts}
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(interval_seconds)
        return {"reports": [], "attempts": attempts, "timed_out": True}

    def close(self) -> None:
        self.client.close()
