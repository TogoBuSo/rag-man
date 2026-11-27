from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mcp_server.server import handle_message


def test_mcp_initialize_and_tools_list() -> None:
    init_resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
    )
    assert init_resp is not None
    assert init_resp["result"]["protocolVersion"] == "2025-03-26"
    assert init_resp["result"]["serverInfo"]["name"] == "rag-man"

    # Notification returns None
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    list_resp = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_resp is not None
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert tool_names == {"list_rag_topics", "search_rag_database", "synthesize_rag_answer"}


def test_mcp_tools_call_forwards_auth_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_MAN_URL", "https://rag.example.test")
    monkeypatch.setenv("RAG_MAN_API_KEY", "rag_test_secret_123")

    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("Authorization"),
                "body": json.loads(request.content.decode("utf-8")) if request.content else None,
            }
        )
        if request.url.path.endswith("/topics"):
            return httpx.Response(200, json=[{"id": "11111111-1111-1111-1111-111111111111", "name": "Biz"}])
        return httpx.Response(200, json=[{"chunk_id": "22222222-2222-2222-2222-222222222222", "content": "Opportunity A"}])

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        topics_resp = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_rag_topics", "arguments": {}},
            },
            client=client,
        )
        assert topics_resp is not None
        assert topics_resp["result"]["isError"] is False
        assert "Biz" in topics_resp["result"]["content"][0]["text"]

        search_resp = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "search_rag_database",
                    "arguments": {
                        "query": "logistics opportunities",
                        "topic_ids": ["11111111-1111-1111-1111-111111111111"],
                        "top_k": 3,
                    },
                },
            },
            client=client,
        )
        assert search_resp is not None
        assert search_resp["result"]["isError"] is False
        assert "Opportunity A" in search_resp["result"]["content"][0]["text"]

    assert captured[0]["url"] == "https://rag.example.test/api/v1/topics"
    assert captured[0]["auth"] == "Bearer rag_test_secret_123"
    assert captured[1]["url"] == "https://rag.example.test/api/v1/rag/retrieve"
    assert captured[1]["body"]["query"] == "logistics opportunities"
    assert captured[1]["body"]["top_k"] == 3
