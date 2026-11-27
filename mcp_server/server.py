#!/usr/bin/env python3
"""Minimal Stdio MCP Server for the rag-man Multi-Tenant RAG Platform.

Exposes three MCP tools over JSON-RPC 2.0 (newline-delimited stdio):
  - list_rag_topics
  - search_rag_database
  - synthesize_rag_answer

Configuration via environment variables:
  - RAG_MAN_URL:     Base URL of the rag-man server (e.g. https://rag.example.com)
  - RAG_MAN_API_KEY: Tenant API key (rag_...)
  - RAG_MAN_TIMEOUT: Optional HTTP timeout in seconds (default: 60)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

SERVER_INFO = {"name": "rag-man", "version": "1.0.0"}
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_rag_topics",
        "description": (
            "List active RAG knowledge-base topics for the authenticated tenant, "
            "including each topic's UUID, name, description, and JSON metadata schema."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_rag_database",
        "description": (
            "Perform hybrid semantic + full-text search over indexed RAG documents "
            "for one or more topic UUIDs. Returns grounded text chunks, scores, and metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "topic_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "One or more topic UUIDs returned by list_rag_topics.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 5,
                    "description": "Number of chunks to return (1-50).",
                },
                "hybrid_alpha": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                    "description": "0.0 = keyword only, 1.0 = vector only, 0.5 = balanced hybrid.",
                },
                "metadata_filters": {
                    "type": "array",
                    "description": "Optional structured JSONB metadata filters.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": ["eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"],
                                "default": "eq",
                            },
                            "value": {},
                        },
                        "required": ["field", "value"],
                    },
                },
                "rerank": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable optional cross-encoder reranking.",
                },
            },
            "required": ["query", "topic_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "synthesize_rag_answer",
        "description": (
            "Run hybrid retrieval plus server-side grounded LLM synthesis over one or more "
            "RAG topics. Returns a synthesized answer and citations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Question or synthesis prompt.",
                },
                "topic_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "One or more topic UUIDs returned by list_rag_topics.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 5,
                },
                "hybrid_alpha": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                },
                "metadata_filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": ["eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"],
                                "default": "eq",
                            },
                            "value": {},
                        },
                        "required": ["field", "value"],
                    },
                },
                "rerank": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["query", "topic_ids"],
            "additionalProperties": False,
        },
    },
]


def _get_config() -> tuple[str, str, float]:
    base_url = os.environ.get("RAG_MAN_URL", "").strip().rstrip("/")
    api_key = os.environ.get("RAG_MAN_API_KEY", "").strip()
    timeout = float(os.environ.get("RAG_MAN_TIMEOUT", "60"))
    if not base_url:
        raise ValueError("RAG_MAN_URL environment variable is not set.")
    if not api_key or api_key.startswith("<"):
        raise ValueError("RAG_MAN_API_KEY environment variable is not configured.")
    return base_url, api_key, timeout


def _call_rag_api(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> Any:
    base_url, api_key, timeout = _get_config()
    url = f"{base_url}/api/v1{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if client is not None:
        resp = client.request(method, url, headers=headers, json=payload, timeout=timeout)
    else:
        with httpx.Client(timeout=timeout) as http_client:
            resp = http_client.request(method, url, headers=headers, json=payload)

    if resp.status_code >= 400:
        raise RuntimeError(f"RAG API error ({resp.status_code}): {resp.text}")
    return resp.json()


def execute_tool(
    name: str,
    arguments: dict[str, Any] | None,
    client: httpx.Client | None = None,
) -> Any:
    args = arguments or {}
    if name == "list_rag_topics":
        return _call_rag_api("GET", "/topics", client=client)

    if name == "search_rag_database":
        payload: dict[str, Any] = {
            "query": args["query"],
            "topic_ids": args["topic_ids"],
            "top_k": args.get("top_k", 5),
            "hybrid_alpha": args.get("hybrid_alpha", 0.5),
            "metadata_filters": args.get("metadata_filters", []),
            "rerank": args.get("rerank", False),
        }
        return _call_rag_api("POST", "/rag/retrieve", payload=payload, client=client)

    if name == "synthesize_rag_answer":
        payload = {
            "query": args["query"],
            "topic_ids": args["topic_ids"],
            "top_k": args.get("top_k", 5),
            "hybrid_alpha": args.get("hybrid_alpha", 0.5),
            "metadata_filters": args.get("metadata_filters", []),
            "rerank": args.get("rerank", False),
            "stream": False,
        }
        return _call_rag_api("POST", "/rag/synthesize", payload=payload, client=client)

    raise KeyError(f"Unknown tool: {name}")


def handle_message(
    msg: dict[str, Any],
    client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    # Notifications have no id and must not receive a response
    if msg_id is None:
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            result = execute_tool(tool_name, tool_args, client=client)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2, ensure_ascii=False),
                        }
                    ],
                    "isError": False,
                },
            }
        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            error_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(error_resp) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
