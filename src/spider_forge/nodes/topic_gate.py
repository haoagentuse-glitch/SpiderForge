"""topic_gate 節點：結構驗證後的單一主題相關性節點。

預設模式 off（通用套件不預先決定「什麼主題才算合格」）；要用的人在請求裡傳
``topic_gate={"mode": "enforce", ...}``，或設 ``SPIDERFORGE_TOPIC_MODE``。

可注入（__init__）：``evaluator``——``(state) -> topic_result`` 的評估函式。
預設 None → late-bind ``shared.topic.evaluate_topic_gate``。換領域判準只換這個。
"""

from __future__ import annotations

from ..state import SpiderForgeState
from .base import Node


class TopicGate(Node):
    def __init__(self, *, evaluator=None):
        self._evaluator = evaluator

    def __call__(self, state: SpiderForgeState) -> dict:
        evaluator = self._evaluator
        if evaluator is None:
            from ..shared.topic import evaluate_topic_gate as evaluator

        result = evaluator(state)
        result["status"] = "topic_validating"
        return result
