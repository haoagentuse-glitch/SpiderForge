"""標準套件 CLI 的離線契約測試。"""

from __future__ import annotations

from pipelines import cli


def t_cli_exposes_all_supported_workflows():
    parser = cli.build_parser()
    parsed = {
        "run": parser.parse_args(
            ["run", "--url", "https://example.com/news"]
        ).command,
        "batch": parser.parse_args(["batch", "cnyes"]).command,
        "status": parser.parse_args(["status"]).command,
        "paths": parser.parse_args(["paths"]).command,
        "train": parser.parse_args(
            ["train-topic", "gold.jsonl"]
        ).command,
    }
    expected = {
        "run": "run",
        "batch": "batch",
        "status": "status",
        "paths": "paths",
        "train": "train-topic",
    }
    return parsed == expected, f"commands={parsed}"


def t_batch_cli_forwards_prefix_and_retry_budget():
    captured = {}
    original = cli.run_batch

    def fake_run_batch(prefixes, *, max_retries, profile, queue_path):
        captured["prefixes"] = prefixes
        captured["max_retries"] = max_retries
        captured["profile"] = profile
        return [{"status": "success"}]

    cli.run_batch = fake_run_batch
    try:
        exit_code = cli.main(
            ["batch", "cnyes", "cna", "--max-retries", "1"]
        )
    finally:
        cli.run_batch = original
    ok = (
        exit_code == 0
        and captured
        == {
            "prefixes": ["cnyes", "cna"],
            "max_retries": 1,
            "profile": "general",
        }
    )
    return ok, f"exit={exit_code} captured={captured}"


def t_batch_cli_forwards_the_selected_profile():
    """--profile finance 要真的傳到 run_batch，而不是被吞掉。"""
    captured = {}
    original = cli.run_batch

    def fake_run_batch(prefixes, *, max_retries, profile, queue_path):
        captured["profile"] = profile
        return [{"status": "success"}]

    cli.run_batch = fake_run_batch
    try:
        exit_code = cli.main(["batch", "--profile", "finance"])
    finally:
        cli.run_batch = original
    return exit_code == 0 and captured == {"profile": "finance"}, (
        f"exit={exit_code} captured={captured}"
    )


def t_site_option_loads_the_given_queue_not_the_default():
    """``--site`` 指到哪份 YAML 就讀哪份。

    踩過的坑：``load_sites`` 用 ``queue_path`` 做存在性檢查，卻用預設的
    ``SITE_QUEUE_PATH`` 讀檔——指定的清單被靜默忽略，跑的是別站。
    其他 batch 測試把 ``load_sites`` 整個換掉，所以照不到這條路徑；
    這裡必須真的落一份檔再讀回來。
    """
    import tempfile
    from pathlib import Path

    from pipelines.batch import load_sites

    with tempfile.TemporaryDirectory() as workdir:
        queue = Path(workdir) / "queue.yaml"
        queue.write_text(
            "sites:\n"
            "  - source_prefix: only_in_this_file\n"
            "    site_url: https://example.com/news\n",
            encoding="utf-8",
        )
        sites = load_sites(queue_path=queue)
        filtered = load_sites(["nothing_matches"], queue_path=queue)

    prefixes = [site["source_prefix"] for site in sites]
    return prefixes == ["only_in_this_file"] and filtered == [], (
        f"prefixes={prefixes} filtered={filtered}"
    )


def t_finance_profile_enforces_the_topic_gate():
    """財經 profile 的實質差異就是主題閘門；通用 profile 不帶任何領域設定。"""
    from pipelines.profiles import apply, resolve

    finance = resolve("finance")
    general = resolve("general")
    merged = apply(finance, {"site_url": "https://example.com"})
    overridden = apply(finance, {"topic_gate": {"mode": "off"}})

    unknown_rejected = False
    try:
        resolve("no-such-profile")
    except ValueError:
        unknown_rejected = True

    return (
        finance["topic_gate"]["mode"] == "enforce"
        and general == {}
        and merged["topic_gate"]["mode"] == "enforce"
        and merged["site_url"] == "https://example.com"
        # 呼叫端明給的值要能推翻 profile（逐站例外不必另開一份 profile）
        and overridden["topic_gate"]["mode"] == "off"
        and unknown_rejected
    ), f"finance={finance} overridden={overridden} unknown_rejected={unknown_rejected}"


def t_doctor_lists_every_provider_role():
    """一個 provider 兼多職時，用途不能互相蓋掉。

    踩過的坑：預設 deepseek 同時負責產碼與第一輪修復，用 dict 直接賦值會讓
    「產碼」這一項憑空消失，報告看起來像是沒有人在產碼。
    """
    from pipelines.doctor import _providers_in_use
    from pipelines.profiles import resolve

    general = _providers_in_use(resolve("general"))
    finance = _providers_in_use(resolve("finance"))
    roles = [role for roles in general.values() for role in roles]

    return (
        sorted(roles) == sorted(["產碼", "第一輪修復", "最後一輪修復"])
        # 通用 profile 不該要求 Gemini（主題閘門預設 off）
        and "gemini" not in general
        # 財經 profile 開了主題閘門，就得多要一把 Gemini 金鑰
        and "gemini" in finance
    ), f"general={general} finance={finance}"


TESTS = [
    t_cli_exposes_all_supported_workflows,
    t_doctor_lists_every_provider_role,
    t_batch_cli_forwards_prefix_and_retry_budget,
    t_batch_cli_forwards_the_selected_profile,
    t_site_option_loads_the_given_queue_not_the_default,
    t_finance_profile_enforces_the_topic_gate,
]
