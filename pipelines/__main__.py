"""``python -m pipelines`` 的入口（等同 ``python -m pipelines.cli``）。"""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
