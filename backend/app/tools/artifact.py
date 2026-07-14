"""
Artifact creation tool — mirrors the createArtifact tool in src/lib/tools.ts.
The content streams through the tool input; execution is a no-op acknowledgement.
"""

from typing import Literal

from langchain_core.tools import tool

ArtifactKind = Literal["code", "html", "markdown", "svg"]


@tool
def create_artifact(
    title: str,
    kind: ArtifactKind,
    content: str,
    language: str | None = None,
) -> dict:
    """创建一个独立的「工件」(artifact) 并在侧边栏展示。当你要输出一段完整的代码文件、
    可运行的 HTML 页面、SVG 图形或较长的 Markdown 文档时，使用此工具而不是把它们
    写在普通回复里。每次只创建一个工件。

    Args:
        title: 工件标题，简短描述其内容
        kind: 工件类型：code=代码片段, html=可预览网页, markdown=文档, svg=矢量图
        content: 工件的完整内容
        language: 当 kind=code 时的编程语言，例如 typescript、python
    """
    # The content itself is rendered on the client from the streamed
    # tool input; here we just acknowledge creation.
    return {"ok": True, "title": title, "kind": kind}
