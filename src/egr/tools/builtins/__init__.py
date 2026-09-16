"""Built-in tools shipped with EGR V1 Alpha.

Every tool is a Tool/Extension: none of them live inside the Core.
"""

from ...domain.tool import ToolSpec
from ..registry import ToolRegistry
from .database_query import DatabaseQueryTool
from .filesystem import FilesystemListTool, FilesystemReadTool, FilesystemWriteTool
from .http_request import HttpRequestTool
from .process_run import ProcessRunTool
from .python_exec import PythonExecuteTool


def register_builtin_tools(registry: ToolRegistry) -> ToolRegistry:
    for tool_class in (
        FilesystemListTool,
        FilesystemReadTool,
        FilesystemWriteTool,
        PythonExecuteTool,
        HttpRequestTool,
        ProcessRunTool,
        DatabaseQueryTool,
    ):
        registry.register(tool_class())
    return registry


__all__ = [
    "DatabaseQueryTool",
    "FilesystemListTool",
    "FilesystemReadTool",
    "FilesystemWriteTool",
    "HttpRequestTool",
    "ProcessRunTool",
    "PythonExecuteTool",
    "ToolSpec",
    "register_builtin_tools",
]
