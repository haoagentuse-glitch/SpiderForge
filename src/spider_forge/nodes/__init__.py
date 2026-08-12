"""節點積木庫：每個 pipeline 節點一個 class（__init__ 存設定 / __call__ 執行）。

**加新節點 = 這裡多一個檔 + 在 pipeline.py 拼裝一行**，不動其他任何東西。
節點彼此不互相 import（有測試鎖住），只由 pipeline.py 組裝；跨節點共用的能力放 shared/。

有的節點自帶邏輯（recon / prepare_request / triage / block_gate / validate），
有的是薄殼、邏輯在 shared/（generate / repair / sandbox …）——形狀一致，
所以 pipeline 拼裝時看不出差別，也隨時可以把邏輯搬進來而不影響呼叫端。
"""

from .base import Node
from .block_gate import ContentBlockGate
from .diagnose import DiagnoseFailure
from .discover_links import DiscoverArticleLinks
from .escalate import EscalateHuman
from .evidence import CollectEvidence
from .fixture import FixtureTest
from .generate import GenerateSpider
from .pagination import VerifyPagination
from .persist import PersistSpider
from .preflight import GenerationPreflight
from .prepare_request import PrepareRequest
from .recon import Recon
from .repair import RepairCode
from .sandbox import SandboxTest
from .strategy import StrategyDecision
from .topic_gate import TopicGate
from .triage import FeasibilityTriage
from .validate import ValidateOutput
from .verify_samples import VerifySamples

__all__ = [
    "Node",
    "PrepareRequest",
    "Recon",
    "DiscoverArticleLinks",
    "VerifySamples",
    "VerifyPagination",
    "FeasibilityTriage",
    "StrategyDecision",
    "CollectEvidence",
    "GenerateSpider",
    "GenerationPreflight",
    "FixtureTest",
    "SandboxTest",
    "ContentBlockGate",
    "ValidateOutput",
    "TopicGate",
    "DiagnoseFailure",
    "RepairCode",
    "PersistSpider",
    "EscalateHuman",
]
