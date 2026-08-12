"""管線層：把 spider_forge 的積木拼成可執行的流程。

刻意**不隨套件安裝**（pyproject 只收 src/）——管線是這個 repo 的應用程式碼，
不是函式庫的一部分。執行時從 repo 根跑：

    python -m pipelines.cli run --url "https://example.com/news"

依賴方向是單向的：pipelines → spider_forge，反過來絕不允許（有測試鎖住）。
"""
