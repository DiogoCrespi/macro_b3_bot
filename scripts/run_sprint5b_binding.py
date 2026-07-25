"""Run Sprint 5B PIT binding and contradiction/temporal checks."""
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.application.bind_mirofish_hypotheses import MiroFishHypothesisBinder
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sets_path = root / "data/audits/mirofish_5a_scenario_sets.json"
    audit_path = root / "data/audits/mirofish_5a_audit.json"
    sets = json.loads(sets_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(sets["scenario_sets"][0]["as_of_timestamp"])
    store = DatabaseStore(Settings().data_dir / "macro_b3_bot.duckdb")
    binder = MiroFishHypothesisBinder(store)
    results = []
    for hypothesis in sets["hypotheses"]:
        binding = binder.bind(hypothesis, as_of)
        hypothesis.update(binding)
        results.append({"hypothesis_id": hypothesis["hypothesis_id"], **binding})
        store.connection.execute(
            "UPDATE scenario_hypotheses SET canonical_payload_json=? WHERE hypothesis_id=?",
            [json.dumps(hypothesis, ensure_ascii=False, sort_keys=True), hypothesis["hypothesis_id"]],
        )
    store.connection.commit()
    store.close()
    sets["hypotheses"] = sets["hypotheses"]
    sets_path.write_text(json.dumps(sets, ensure_ascii=False, indent=2), encoding="utf-8")
    audit["sprint_5b_binding"] = {
        "as_of_timestamp": sets["scenario_sets"][0]["as_of_timestamp"],
        "results": results,
        "decision_safety": "UNVERIFIED_OR_PARTIAL_HYPOTHESES_DO_NOT_INFLUENCE_DECISIONS",
    }
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
