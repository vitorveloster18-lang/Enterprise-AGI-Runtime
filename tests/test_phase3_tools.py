"""Fase 3 — Tool Runtime: sandbox, git, e-mail, browser, MCP e custo de ferramentas."""

from __future__ import annotations

import subprocess
import sys

import pytest

from egr.core.config import MCPConfig, MCPServerConfig
from egr.domain.enums import DecisionType
from egr.domain.policy import Policy, PolicyRule
from egr.tools.mcp import MCPClient, connect_mcp_servers
from egr.tools.sandbox import SandboxRunner

# --------------------------------------------------------------------- sandbox


def test_sandbox_auto_falls_back_to_process_without_container_runtime(runtime, monkeypatch):
    monkeypatch.setattr(SandboxRunner, "runtime_available", lambda self: None)
    info = runtime.sandbox_info
    assert info["configured"] == "auto"
    assert info["mode"] == "process"
    assert info["runtime_available"] is False


def test_sandbox_process_executes_and_collects_artifacts(runtime):
    runner = SandboxRunner(
        runtime.settings.config.tools.sandbox,
        workspace=runtime.settings.workspace,
        sandbox_dir=runtime.settings.sandbox_path,
        artifacts_dir=runtime.settings.artifacts_path,
        task_id="tsk-sandbox",
    )
    result = runner.run("from pathlib import Path\nPath('saida.md').write_text('# ok')\nprint('done')\n")

    assert result.ok, result.error
    assert "done" in result.stdout
    assert result.mode == "process"
    assert result.artifacts
    assert (runtime.settings.artifacts_path / "tsk-sandbox" / "saida.md").exists()


def test_sandbox_process_filters_secrets_from_environment(runtime, monkeypatch):
    monkeypatch.setenv("EGR_TEST_API_KEY", "nao-deve-vazar")
    runner = SandboxRunner(
        runtime.settings.config.tools.sandbox,
        workspace=runtime.settings.workspace,
        sandbox_dir=runtime.settings.sandbox_path,
        artifacts_dir=runtime.settings.artifacts_path,
    )
    result = runner.run("import os\nprint(os.environ.get('EGR_TEST_API_KEY', 'VAZOU'))\n")
    assert "nao-deve-vazar" not in result.stdout
    assert "VAZOU" in result.stdout


def test_sandbox_container_mode_without_runtime_fails_loudly(runtime, monkeypatch):
    monkeypatch.setattr(SandboxRunner, "runtime_available", lambda self: None)
    config = runtime.settings.config.tools.sandbox.model_copy(update={"mode": "container"})
    runner = SandboxRunner(
        config,
        workspace=runtime.settings.workspace,
        sandbox_dir=runtime.settings.sandbox_path,
        artifacts_dir=runtime.settings.artifacts_path,
    )
    result = runner.run("print('oi')")
    assert not result.ok
    assert "nenhum runtime disponível" in result.error


# ------------------------------------------------------------------------- git


@pytest.fixture()
def git_repo(runtime):
    workspace = runtime.settings.workspace
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "egr@test"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "EGR"], cwd=workspace, check=True)
    (workspace / "arquivo.txt").write_text("conteudo\n", encoding="utf-8")
    return workspace


def test_git_status_and_diff(runtime, git_repo):
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-git")

    status = runtime.tools.execute("git.status", {}, ctx)
    assert status.ok, status.error
    assert "arquivo.txt" in status.output["stdout"]

    runtime.tools.execute("git.commit", {"message": "primeiro commit"}, ctx)
    (git_repo / "arquivo.txt").write_text("mudou\n", encoding="utf-8")

    diff = runtime.tools.execute("git.diff", {}, ctx)
    assert diff.ok
    assert "mudou" in diff.output["stdout"]


def test_git_log_returns_structured_commits(runtime, git_repo):
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-git")
    runtime.tools.execute("git.commit", {"message": "commit de teste"}, ctx)

    log = runtime.tools.execute("git.log", {"limit": 5}, ctx)
    assert log.ok
    assert log.output["commits"]
    assert log.output["commits"][0]["subject"] == "commit de teste"


def test_git_commit_requires_approval_but_status_is_free(runtime):
    _, commit = runtime.request_action("git.commit", {"message": "x"})
    assert commit.needs_approval
    assert commit.required_role == "operator"

    _, status = runtime.request_action("git.status", {})
    assert status.allowed


# ----------------------------------------------------------------------- email


def test_email_is_disabled_by_default(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-mail")
    result = runtime.tools.execute("email.send", {"to": "a@b.com", "subject": "x", "body": "y"}, ctx)
    assert not result.ok
    assert "disabled" in result.error


def test_email_send_works_and_reports_cost(runtime, monkeypatch):
    runtime.settings.config.tools.email.enabled = True
    runtime.settings.config.tools.email.smtp_host = "smtp.test"
    runtime.settings.config.tools.email.cost_per_send = 0.002

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, message, from_addr=None, to_addrs=None):
            sent["to"] = to_addrs
            sent["subject"] = message["Subject"]

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-mail")
    result = runtime.tools.execute(
        "email.send", {"to": "financeiro@acme.com", "subject": "Relatório", "body": "segue"}, ctx
    )

    assert result.ok, result.error
    assert result.cost == 0.002
    assert sent["to"] == ["financeiro@acme.com"]
    assert sent["tls"] is True

    _, decision = runtime.request_action("email.send", {"to": "a@b.com"})
    assert decision.needs_approval


# --------------------------------------------------------------------- browser


def test_browser_requires_opt_in(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-browser")
    result = runtime.tools.execute("browser.navigate", {"url": "https://example.com"}, ctx)
    assert not result.ok
    assert "disabled" in result.error


def test_browser_without_playwright_gives_install_hint(runtime):
    runtime.settings.config.tools.browser.enabled = True
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-browser")
    result = runtime.tools.execute("browser.navigate", {"url": "https://example.com"}, ctx)
    assert not result.ok
    assert "playwright" in result.error.lower()


def test_browser_respects_allowed_domains(runtime):
    runtime.settings.config.tools.browser.enabled = True
    runtime.settings.config.tools.browser.allowed_domains = ["acme.com"]
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-browser")
    result = runtime.tools.execute("browser.navigate", {"url": "https://evil.com/x"}, ctx)
    assert not result.ok
    assert "fora da lista permitida" in result.error


# ------------------------------------------------------------------------- mcp

FAKE_MCP_SERVER = '''
import json, sys

TOOLS = [
    {"name": "somar", "description": "Soma dois números",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                     "required": ["a", "b"]}},
]

def respond(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        respond({"jsonrpc": "2.0", "id": request_id,
                 "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake"}}})
    elif method == "tools/list":
        respond({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        args = request.get("params", {}).get("arguments", {})
        total = float(args.get("a", 0)) + float(args.get("b", 0))
        respond({"jsonrpc": "2.0", "id": request_id,
                 "result": {"content": [{"type": "text", "text": str(total)}]}})
'''


@pytest.fixture()
def fake_mcp_server(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_MCP_SERVER, encoding="utf-8")
    return script


def test_mcp_client_lists_and_calls_tools(fake_mcp_server):
    client = MCPClient(MCPServerConfig(name="fake", command=sys.executable, args=[str(fake_mcp_server)]))
    tools = client.list_tools()
    assert [tool["name"] for tool in tools] == ["somar"]

    result = client.call_tool("somar", {"a": 2, "b": 3})
    assert result["content"][0]["text"] == "5.0"
    client.stop()


def test_mcp_tools_are_blocked_by_default_deny(runtime, fake_mcp_server):
    config = MCPConfig(servers=[MCPServerConfig(name="fake", command=sys.executable, args=[str(fake_mcp_server)])])
    proxies, failures = connect_mcp_servers(config)
    assert not failures
    for proxy in proxies:
        runtime.tools.register(proxy)

    assert runtime.tools.has("mcp.fake.somar")

    _, decision = runtime.request_action("mcp.fake.somar", {"a": 1, "b": 2})
    assert decision.decision == DecisionType.DENY
    assert "default deny" in decision.reason


def test_mcp_tools_execute_after_policy_grants_access(runtime, fake_mcp_server):
    config = MCPConfig(servers=[MCPServerConfig(name="fake", command=sys.executable, args=[str(fake_mcp_server)])])
    proxies, _ = connect_mcp_servers(config)
    for proxy in proxies:
        runtime.tools.register(proxy)

    runtime.policy.add_policy(
        Policy(
            id="mcp-lab",
            priority=60,
            rules=[PolicyRule(id="mcp-allow", action="mcp.fake.*", decision=DecisionType.ALLOW)],
        )
    )
    _, decision = runtime.request_action("mcp.fake.somar", {"a": 1, "b": 2})
    assert decision.allowed

    ctx = runtime.adhoc_tool_context("development", task_id="tsk-mcp")
    result = runtime.tools.execute("mcp.fake.somar", {"a": 1, "b": 2}, ctx)
    assert result.ok, result.error
    assert result.output["text"] == "3.0"


# ------------------------------------------------------------ custo de tools


class PaidTool:
    """Ferramenta de mentira que cobra por chamada."""

    def __init__(self):
        from egr.domain.enums import RiskLevel
        from egr.domain.tool import ToolSpec

        self.spec = ToolSpec(name="fake.paid", description="cobra por chamada", risk=RiskLevel.LOW)

    def validate(self, args):
        return None

    def execute(self, request, ctx):
        from egr.domain.tool import ToolResult

        return ToolResult.success({"ok": True}, cost=1.25)


def test_tool_cost_is_accumulated_in_the_task(runtime):
    from egr.domain.agent import AgentPermissions, AgentSpec

    runtime.tools.register(PaidTool())
    runtime.policy.add_policy(
        Policy(
            id="test-tools",
            priority=90,
            rules=[PolicyRule(id="paid-allow", action="fake.*", decision=DecisionType.ALLOW)],
        )
    )
    runtime.register_agent(
        AgentSpec(id="paid-agent", objective="teste", permissions=AgentPermissions(tools=["fake.*"]))
    )

    task = runtime.task_engine.create("task com custo de ferramenta", agent_id="paid-agent")
    task.context["plan"] = {
        "objective": task.objective,
        "steps": [{"id": "s1", "tool": "fake.paid", "args": {}, "rationale": "teste"}],
        "final_answer": "ok",
    }
    runtime.tasks.save(task)
    task = runtime.agent_engine.run(task)

    assert task.result.tool_cost == 1.25
    assert task.result.steps[0].cost == 1.25
    assert task.result.total_cost >= 1.25
