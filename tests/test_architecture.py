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


def t_prompt_contains_no_hardcoded_field_names():
    """產碼 prompt 不得自己列欄位——欄位段落必須由 schema 生成。

    原專案（與本專案重構前）把欄位名寫死在 SPIDER_CONTRACT 的文字裡，schema 只
    供驗證使用。兩條路不同步的後果不是「改了沒效果」，而是**必定失敗的迴圈**：
    驗證開始要求新欄位、模型卻不知道要抓它，修復用的又是同一份 prompt，
    兩輪燒完進死信。這個測試釘死「prompt 不再自己列欄位」。
    """
    from spider_forge.prompts.generate import SPIDER_CONTRACT

    # 直接比對欄位名會被 start_urls / content_scope 這類子字串誤判，所以改看
    # 「欄位專屬的語意字眼」——這些只該出現在 schema 的 rule 裡。
    field_specific_wording = ("ISO8601", "忠實摘錄", "permalink", "模型摘要")
    leaked = [word for word in field_specific_wording if word in SPIDER_CONTRACT]
    has_placeholder = "{field_contract}" in SPIDER_CONTRACT
    return has_placeholder and not leaked, (
        f"placeholder={has_placeholder} 洩漏到通用 prompt 的欄位語意={leaked}"
    )


def t_changing_the_schema_reaches_both_validation_and_prompt():
    """**改一個地方就生效**：加一個欄位，驗證與產碼 prompt 必須同時看見它。

    這是 schemas/outputs.py 的 Article 作為唯一來源的核心保證。
    """
    from spider_forge.schemas.outputs import build_target_schema, field_contract_block
    from spider_forge.shared.fixture import build_fixture_spec
    from spider_forge.shared.generation import _contract

    schema = build_target_schema()
    schema["fields"]["author"] = {
        "type": "string",
        "required": True,
        "rule": "作者署名，取不到就跳過該筆",
    }

    # ① 驗證路徑：離線重播的必填欄位
    fixture = build_fixture_spec(
        {
            "site_url": "https://example.com/news",
            "target_schema": schema,
            "recon_report": {"dom_excerpt": "<ul></ul>"},
        }
    )
    # ② 生成路徑：產碼 prompt
    contract = _contract(
        {
            "target_schema": schema,
            "source_prefix": "example_com",
            "site_name": "Example",
        }
    )

    return (
        "author" in fixture["required_fields"]
        and "author" in contract
        and "作者署名" in contract
        # 規則裡的 {max_chars} 要被實際上限取代，不能把大括號漏進 prompt
        and "{max_chars}" not in field_contract_block(schema)
        and "最多 6000 字" in contract
    ), (
        f"required_fields={fixture['required_fields']} "
        f"prompt 有 author={'author' in contract}"
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
    t_prompt_contains_no_hardcoded_field_names,
    t_changing_the_schema_reaches_both_validation_and_prompt,
    t_state_layers_partition_the_whole_state,
    t_graph_entry_only_accepts_input_fields,
    t_forge_result_returns_only_output_fields,
    t_control_plane_has_no_crawler_runtime_import,
    t_library_never_imports_the_pipeline_layer,
    t_execution_entries_only_depend_on_pipeline,
    t_pipeline_layer_is_not_installed_as_a_package,
    t_package_root_only_contains_public_control_modules,
]
