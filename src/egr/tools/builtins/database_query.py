"""Read-only SQL access to the runtime database.

Only SELECT/WITH statements; the connection is opened in read-only mode.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

READONLY_PREFIXES = ("select", "with", "pragma", "explain")
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|vacuum|replace)\b", re.IGNORECASE)


class DatabaseQueryTool(Tool):
    spec = ToolSpec(
        name="database.query",
        description="Consulta somente-leitura (SELECT) no banco do Runtime",
        parameters={
            "sql": {"type": "string", "required": True},
            "limit": {"type": "integer", "required": False},
        },
        risk=RiskLevel.MEDIUM,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        sql = (request.args.get("sql") or "").strip().rstrip(";")
        if not sql:
            return ToolResult.failure("sql is required")
        if not sql.lower().startswith(READONLY_PREFIXES) or FORBIDDEN.search(sql):
            return ToolResult.failure("only read-only SELECT/WITH statements are allowed")
        if ctx.database_path is None or not Path(ctx.database_path).exists():
            return ToolResult.failure("runtime database not available")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "sql": sql})

        limit = int(request.args.get("limit", 200))
        uri = f"file:{Path(ctx.database_path).resolve()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=10.0)
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(sql)
            rows = cursor.fetchmany(limit)
            columns = [description[0] for description in cursor.description or []]
            connection.close()
        except sqlite3.Error as exc:
            return ToolResult.failure(f"query failed: {exc}")

        return ToolResult.success(
            {
                "columns": columns,
                "rows": [dict(row) for row in rows],
                "rowcount": len(rows),
                "limit": limit,
            }
        )
