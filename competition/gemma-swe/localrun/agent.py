"""O loop orquestrador→analisador→coder→revisor, lendo os MESMOS prompts do zip.

Fonte única de verdade: editar `../prompts/*.md` muda o harness E a submissão.
"""

from __future__ import annotations

import re
from pathlib import Path

from .llm import LLMClient, first_choice, tool_calls_of
from .tools import Budget, ToolExecutor, tool_schemas

PROMPTS = Path(__file__).parent.parent / "prompts"

ANALYZER_TOOLS = {
    "read_file",
    "get_code_neighbors",
    "search_similar_code",
    "get_code_subgraph",
}
CODER_TOOLS = {"read_file", "edit_file", "write_file", "run_command"}
REVIEWER_TOOLS = {"read_file", "get_code_neighbors", "get_code_subgraph", "run_command"}

VERDICT = re.compile(r"VERDICT:\s*(accept|revise)", re.IGNORECASE)


def read_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def run_role(
    llm: LLMClient,
    tools: ToolExecutor,
    budget: Budget,
    system: str,
    user: str,
    allowed: set[str],
    max_turns: int,
    max_tokens: int,
    temperature: float,
) -> str:
    """Um papel até o fim (sem mais tool-calls) ou o teto. Devolve o texto final."""
    schemas = [schema for schema in tool_schemas() if schema["function"]["name"] in allowed]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    final = ""
    while budget.turns < max_turns and llm.total_tokens < max_tokens:
        budget.turns += 1
        message = first_choice(llm.chat(messages, tools=schemas, temperature=temperature))
        calls = [call for call in tool_calls_of(message) if call["name"] in allowed]
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": message.get("tool_calls") or [],
            }
        )
        if not calls:
            final = message.get("content") or ""
            break
        for call in calls:
            result = tools.call(call["name"], call["args"])
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result[:8000]}
            )
    return final


def run_task(
    llm: LLMClient,
    tools: ToolExecutor,
    budget: Budget,
    problem: str,
    max_turns: int,
    max_tokens: int,
    temperature: float,
    max_rounds: int = 3,
) -> dict:
    system = read_prompt("system.md")
    _ = system  # o orquestrador do harness É este loop; o system.md vale no avaliador
    analysis = run_role(
        llm, tools, budget, read_prompt("analyzer.md"),
        f"TASK:\n{problem}", ANALYZER_TOOLS, max_turns, max_tokens, temperature,
    )
    verdict, notes, rounds = "accept", "", 0
    patch_report = ""
    for round_no in range(1, max_rounds + 1):
        rounds = round_no
        coder_input = f"ANALYSIS:\n{analysis}\n\nROUND: {round_no}/{max_rounds}"
        if notes:
            coder_input += f"\n\nREVIEWER NOTES (address every one):\n{notes}"
        patch_report = run_role(
            llm, tools, budget, read_prompt("coder.md"),
            coder_input, CODER_TOOLS, max_turns, max_tokens, temperature,
        )
        review = run_role(
            llm, tools, budget, read_prompt("reviewer.md"),
            f"ROUND: {round_no}/{max_rounds}\n\nPATCH REPORT:\n{patch_report}",
            REVIEWER_TOOLS, max_turns, max_tokens, temperature,
        )
        match = VERDICT.search(review)
        verdict = match.group(1).lower() if match else "accept"
        notes = review
        if verdict == "accept":
            break
    patch = tools.call("submit_patch", {})
    return {
        "rounds": rounds,
        "verdict": verdict,
        "turns": budget.turns,
        "tokens": llm.total_tokens,
        "patch_chars": len(patch),
        "empty": patch.startswith("empty diff"),
    }
