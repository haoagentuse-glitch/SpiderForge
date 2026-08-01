"""模組邊界測試：防止重構後重新耦合。"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "spider_forge"
PIPELINES_DIR = REPO_ROOT / "pipelines"
NODES_DIR = PACKAGE_DIR / "nodes"
# 套件根只放「函式庫的公共模組」；管線與 CLI 在 repo 根的 pipelines/。
ROOT_PYTHON_FILES = {
    "__init__.py",
    "config.py",
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


def t_library_never_imports_the_pipeline_layer():
    """**依賴單向**：pipelines → spider_forge，反過來絕不允許。

    這是「函式庫 / 管線」分家的核心不變式。一旦函式庫 import 了 pipelines，
    `pip install spider_forge` 就會壞掉（pipelines 不隨套件安裝）。
    """
    violations: list[str] = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
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
                if module.split(".", 1)[0] == "pipelines":
                    violations.append(
                        f"{path.relative_to(PACKAGE_DIR)}:{node.lineno}:{module}"
                    )
    return not violations, f"violations={violations}"


def t_execution_entries_only_depend_on_pipeline():
    """CLI 與批次執行器只透過 pipeline.py 取得流程，不自己 import 節點。"""
    violations: list[str] = []
    for path in (
        PIPELINES_DIR / "__main__.py",
        PIPELINES_DIR / "cli.py",
        PIPELINES_DIR / "batch.py",
        PACKAGE_DIR / "tools" / "topic_training.py",
    ):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.startswith("nodes") or ".nodes" in module:
                violations.append(f"{path.name}:{node.lineno}:{module}")
    return not violations, f"violations={violations}"


def t_pipeline_layer_is_not_installed_as_a_package():
    """pipelines/ 是這個 repo 的應用程式碼，不隨套件發布（pyproject 只收 src/）。"""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declares_src_only = 'where = ["src"]' in pyproject
    mentions_pipelines = "pipelines" in pyproject
    return declares_src_only and not mentions_pipelines, (
        f'where=["src"]:{declares_src_only} pyproject 提到 pipelines:{mentions_pipelines}'
    )


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
    from pipelines import pipeline
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


def t_generation_prompt_field_list_matches_the_schema():
    """產碼 prompt 列的欄位必須與 schema 一致。

    缺口說明：`_contract()` 只把 source_type / content_scope / max_content_chars
    從 schema 帶進 prompt，**欄位名是 SPIDER_CONTRACT 的文字寫死的**。所以
    「改抓什麼欄位只改 schemas/outputs.py」在 fields 這一層並不成立——改了 schema
    卻沒改 prompt，模型仍會照舊欄位產碼，而且不會有任何錯誤提示。

    這個測試不修掉那個耦合（動 prompt 會影響產碼品質，要有實跑證據才敢改），
    但至少讓不一致無法靜默發生。
    """
    import re

    from spider_forge.prompts.generate import SPIDER_CONTRACT
    from spider_forge.schemas import DEFAULT_TARGET_SCHEMA

    match = re.search(r"欄位為\s*(.+?)；", SPIDER_CONTRACT, re.DOTALL)
    if not match:
        return False, "SPIDER_CONTRACT 找不到「欄位為 …；」這段，欄位契約可能被改寫"
    in_prompt = {
        field.strip()
        for field in re.split(r"[、,]", match.group(1).replace("\n", ""))
        if field.strip()
    }
    in_schema = set(DEFAULT_TARGET_SCHEMA["fields"])
    return in_prompt == in_schema, (
        f"prompt={sorted(in_prompt)} schema={sorted(in_schema)}"
        "（改了 schema 的 fields 就要同步改 prompts/generate.py 的 SPIDER_CONTRACT）"
    )


def _env_vars_read_by_code() -> set[str]:
    """AST 掃出程式碼真正讀取的環境變數（含 registry 的 api_key_env 間接讀取）。"""
    read: set[str] = set()
    for path in PACKAGE_DIR.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                    base = func.value
                    if isinstance(base, ast.Attribute):
                        name = f"{base.attr}.{name}"
                    elif isinstance(base, ast.Name):
                        name = f"{base.id}.{name}"
                if name in {"os.getenv", "environ.get", "os.environ.get"}:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        read.add(first.value)
            if isinstance(node, ast.keyword) and node.arg == "api_key_env":
                if isinstance(node.value, ast.Constant):
                    read.add(node.value.value)
    return read


def t_env_example_has_no_dead_variables():
    """.env.example 不得出現程式碼不讀的變數。

    踩過的坑：example 的變數名與程式碼實際讀的名字不一致，照著填會拿不到金鑰
    且完全沒有錯誤提示。明確標為「預留」的例外列在 RESERVED。
    """
    import re

    reserved = {"OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "DATABASE_URL"}
    example = PACKAGE_DIR.parents[1] / ".env.example"
    if not example.is_file():
        return False, f"找不到 {example}"
    declared = [
        match.group(1)
        for line in example.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    ]
    read = _env_vars_read_by_code()
    dead = sorted(set(declared) - read - reserved)
    return not dead, f"程式碼不讀卻出現在 .env.example：{dead}"


TESTS = [
    t_nodes_do_not_import_other_nodes,
    t_env_example_has_no_dead_variables,
    t_generation_prompt_field_list_matches_the_schema,
    t_state_layers_partition_the_whole_state,
    t_graph_entry_only_accepts_input_fields,
    t_forge_result_returns_only_output_fields,
    t_control_plane_has_no_crawler_runtime_import,
    t_library_never_imports_the_pipeline_layer,
    t_execution_entries_only_depend_on_pipeline,
    t_pipeline_layer_is_not_installed_as_a_package,
    t_package_root_only_contains_public_control_modules,
]
