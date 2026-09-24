"""Servidor MCP de exemplo — exposto ao EGR como ferramentas governadas.

Rode `egr mcp list` para ver as ferramentas descobertas e `egr mcp call` para
executá-las. Sem regra de política, o Runtime bloqueia (default deny): é a
política `mcp-lab` (em policies/) que libera este servidor.

Protocolo: JSON-RPC 2.0 sobre stdio (transporte mais comum em MCP).
"""

import json
import sys

TOOLS = [
    {
        "name": "somar",
        "description": "Soma dois números",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "percentual",
        "description": "Calcula a variação percentual entre dois valores",
        "inputSchema": {
            "type": "object",
            "properties": {"anterior": {"type": "number"}, "atual": {"type": "number"}},
            "required": ["anterior", "atual"],
        },
    },
]


def respond(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def handle(method: str, params: dict) -> dict:
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "somar":
            value = float(args.get("a", 0)) + float(args.get("b", 0))
        elif name == "percentual":
            anterior = float(args.get("anterior", 0))
            atual = float(args.get("atual", 0))
            value = ((atual - anterior) / anterior * 100) if anterior else 0.0
        else:
            raise ValueError(f"ferramenta desconhecida: {name}")
        return {"content": [{"type": "text", "text": str(round(value, 4))}]}
    raise ValueError(f"método não suportado: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            respond(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "calculadora", "version": "0.1.0"},
                    },
                }
            )
            continue
        if method == "notifications/initialized":
            continue
        try:
            respond({"jsonrpc": "2.0", "id": request_id, "result": handle(method, request.get("params") or {})})
        except Exception as exc:  # noqa: BLE001
            respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}})


if __name__ == "__main__":
    main()
