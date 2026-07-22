from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path


KEYWORDS = {
    "tribunal": "decision_consensus",
    "risk": "risk_management",
    "news": "news_ingestion",
    "fetch": "data_ingestion",
    "scrap": "data_ingestion",
    "adapter": "integration_adapter",
    "cache": "cache",
    "logger": "observability",
    "metric": "observability",
    "macro": "macro_analysis",
    "portfolio": "portfolio_management",
    "alloc": "portfolio_management",
    "mirofish": "scenario_simulation",
}


@dataclass(slots=True)
class Candidate:
    repository: str
    path: str
    language: str
    capability: str
    sha256: str
    imports: list[str]
    functions: list[str]
    classes: list[str]


def classify(path: Path) -> str:
    name = path.name.lower()
    for keyword, capability in KEYWORDS.items():
        if keyword in name:
            return capability
    return "unclassified"


def inspect_python(path: Path) -> tuple[list[str], list[str], list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return [], [], []
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    return sorted(set(imports)), sorted(set(functions)), sorted(set(classes))


def discover(repository_name: str, root: Path) -> list[Candidate]:
    if not root.exists():
        return []
    candidates: list[Candidate] = []
    allowed = {".py", ".js", ".ts", ".json"}
    ignored = {"node_modules", ".git", ".venv", ".venv_stable", "dist", "build", "coverage"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in ignored for part in path.parts):
            continue
        capability = classify(path)
        if capability == "unclassified":
            continue
        content = path.read_bytes()
        imports: list[str] = []
        functions: list[str] = []
        classes: list[str] = []
        if path.suffix.lower() == ".py":
            imports, functions, classes = inspect_python(path)
        candidates.append(
            Candidate(
                repository=repository_name,
                path=str(path.relative_to(root)),
                language=path.suffix.lower().lstrip("."),
                capability=capability,
                sha256=hashlib.sha256(content).hexdigest(),
                imports=imports,
                functions=functions,
                classes=classes,
            )
        )
    return sorted(candidates, key=lambda item: (item.capability, item.path))


def write_manifest(candidates: list[Candidate], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "candidates": [asdict(item) for item in candidates]}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    btc_bot_root = Path(r"C:\Nestjs\Advanced_Btc_Bot\Advanced_Btc_Bot")
    screener_root = Path(r"C:\Nestjs\b3_screener")
    
    candidates = []
    candidates.extend(discover("Advanced_Btc_Bot", btc_bot_root))
    candidates.extend(discover("b3_screener", screener_root))
    
    manifest_file = base_dir / "data" / "reuse_manifest.json"
    write_manifest(candidates, manifest_file)
    print(f"Manifesto de Reuso Gerado com sucesso com {len(candidates)} componentes em: {manifest_file}")

