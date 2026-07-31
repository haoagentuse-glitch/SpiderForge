"""模組邊界測試：防止重構後重新耦合。"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "spider_forge"
NODES_DIR = PACKAGE_DIR / "nodes"
ROOT_PYTHON_FILES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "pipeline.py",
    "state.py",
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def t_nodes_do_not_import_other_nodes():
    """積木不互相 import，只由 pipeline.py 組裝（鐵律3）。base.py 是共同基類，不算節點。"""
    violations: list[str] = []
    for path in NODES_DIR.glob("*.py"):
        if path.name in {"__init__.py", "base.py"}:
            continue
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            same_package = node.level == 1 and module not in {"base", ""}
            if same_package or module.startswith("spider_forge.nodes"):
                violations.append(f"{path.name}:{node.lineno}:{module}")
    return not violations, f"violations={violations}"


def t_control_plane_has_no_crawler_runtime_import():
    violations: list[str] = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if path == Path(__file__) or "_history" in path.parts or "__pycache__" in path.parts:
            continue
        for node in ast.walk(_tree(path)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for module in modules:
                root = module.split(".", 1)[0]
                if root in {"crawler_runtime", "news_crawler"}:
                    violations.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}:{module}"
                    )
    return not violations, f"violations={violations}"


def t_execution_entries_only_depend_on_pipeline():
    violations: list[str] = []
    for path in (
        PACKAGE_DIR / "__main__.py",
        PACKAGE_DIR / "cli.py",
        PACKAGE_DIR / "runs" / "batch.py",
        PACKAGE_DIR / "tools" / "topic_training.py",
    ):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.startswith("nodes") or ".nodes" in module:
                violations.append(
                    f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}:{module}"
                )
    return not violations, f"violations={violations}"


def t_package_root_only_contains_public_control_modules():
    actual = {path.name for path in PACKAGE_DIR.glob("*.py")}
    unexpected = sorted(actual - ROOT_PYTHON_FILES)
    missing = sorted(ROOT_PYTHON_FILES - actual)
    return not unexpected and not missing, (
        f"unexpected={unexpected} missing={missing}"
    )


def t_state_layers_partition_the_whole_state():
    """input/internal/output 三層互斥且聯集等於完整 state（階段7）。"""
    from spider_forge.state import (
        ForgeInput,
        ForgeInternal,
        ForgeOutput,
        SpiderForgeState,
    )

    layers = {
        "input": set(ForgeInput.__annotations__),
        "internal": set(ForgeInternal.__annotations__),
        "output": set(ForgeOutput.__annotations__),
    }
    overlaps = {
        f"{a}&{b}": sorted(layers[a] & layers[b])
        for a, b in (("input", "internal"), ("input", "output"), ("internal", "output"))
        if layers[a] & layers[b]
    }
    union = set().union(*layers.values())
    missing = sorted(set(SpiderForgeState.__annotations__) - union)
    return not overlaps and not missing, f"overlaps={overlaps} missing={missing}"


def t_graph_entry_only_accepts_input_fields():
    """graph 入口擋掉內部欄位：呼叫端不能（也不必）自己塞 retry_count 這類狀態。"""
    from spider_forge import pipeline
    from spider_forge.state import ForgeInput

    accepted = set(pipeline.build_pipeline().get_input_jsonschema()["properties"])
    leaked = sorted(accepted - set(ForgeInput.__annotations__))
    return not leaked and "retry_count" not in accepted, f"leaked={leaked}"


def t_forge_result_returns_only_output_fields():
    from spider_forge.state import ForgeOutput, forge_result

    result = forge_result(
        {
            "run_id": "r1",
            "status": "success",
            "spider_path": "p",
            "retry_count": 3,
            "recon_report": {"x": 1},
        }
    )
    allowed = {"run_id", *ForgeOutput.__annotations__}
    return set(result) <= allowed and set(result) == {
        "run_id",
        "status",
        "spider_path",
    }, f"result_keys={sorted(result)}"


TESTS = [
    t_nodes_do_not_import_other_nodes,
    t_state_layers_partition_the_whole_state,
    t_graph_entry_only_accepts_input_fields,
    t_forge_result_returns_only_output_fields,
    t_control_plane_has_no_crawler_runtime_import,
    t_execution_entries_only_depend_on_pipeline,
    t_package_root_only_contains_public_control_modules,
]
