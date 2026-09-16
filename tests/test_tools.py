def test_filesystem_list_and_read(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="test-tools")
    result = runtime.tools.execute("filesystem.list", {"path": "documents"}, ctx)
    assert result.ok
    assert result.output["count"] == 3

    result = runtime.tools.execute("filesystem.read", {"path": "documents/nota-fiscal-82731.md"}, ctx)
    assert result.ok
    assert "Nota Fiscal" in result.output["content"]


def test_filesystem_confines_paths_to_the_workspace(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="test-tools")
    for path in ("/etc/passwd", "../../etc/passwd", "documents/../../etc/passwd"):
        result = runtime.tools.execute("filesystem.read", {"path": path}, ctx)
        assert not result.ok
        assert "escapes" in result.error


def test_python_execution_is_sandboxed_and_produces_artifacts(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="test-py")
    result = runtime.tools.execute(
        "python.execute",
        {"script": "from pathlib import Path\nPath('saida.md').write_text('# ok')\nprint('done')\n"},
        ctx,
    )
    assert result.ok, result.error
    assert "done" in result.output["stdout"]
    assert result.artifacts
    assert (runtime.settings.artifacts_path / "test-py" / "saida.md").exists()


def test_write_outside_allowed_roots_is_refused(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="test-write")
    result = runtime.tools.execute(
        "filesystem.write", {"path": "../forbidden.txt", "content": "x"}, ctx
    )
    assert not result.ok
    assert "outside" in result.error


def test_database_tool_is_read_only(runtime):
    runtime.submit("tarefa de teste")
    ctx = runtime.adhoc_tool_context("development", task_id="test-db")
    result = runtime.tools.execute("database.query", {"sql": "select id from tasks limit 1"}, ctx)
    assert result.ok
    assert result.output["rowcount"] == 1

    result = runtime.tools.execute("database.query", {"sql": "delete from tasks"}, ctx)
    assert not result.ok
    assert "read-only" in result.error


def test_unknown_tool_is_reported(runtime):
    ctx = runtime.adhoc_tool_context("development", task_id="test-unknown")
    result = runtime.tools.execute("nao.existe", {}, ctx)
    assert not result.ok
    assert "unknown tool" in result.error
