"""Reusable ``search_documents`` agent tool factory (task-17-brief.md req. 5).

Wired into ``app.agents.rm.agent.RMAgent`` in this task. IE and quality own
their own agent modules concurrently (Task 15); wiring the same tool into
them is a one-line addition to their ``tools()`` — a documented follow-up,
not done here to avoid touching files another in-flight agent owns.

The excerpt returned to the model is untrusted document text passed as
tool-result *data* (the agent loop already treats every tool result as
untrusted and redacts/JSON-encodes it before it reaches the model — see
``app.agents.base.BaseAgent._run_loop``); it is never interpreted as an
instruction, and the model can only ever cite one of the ``evidence_id``s
handed back here (validated in ``app.agents.base._validate_submission``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, create_model

from app.agents.base import AgentContext, AgentTool, ToolError, ToolResult
from app.orchestration.protocol import EvidenceRef

MAX_EXCERPT_CHARS = 600
DEFAULT_K = 4
MAX_K = 10


def make_search_documents_tool(*, default_query: str, default_k: int = DEFAULT_K) -> AgentTool:
    """Build a ``search_documents`` tool bound to one agent's default query.

    ``default_query`` is the agent-specific fallback the model can use
    as-is (e.g. RM's "material shortage replenishment reservation policy").
    """
    input_model = create_model(
        "SearchDocumentsInput",
        query=(str, Field(default=default_query, min_length=1, max_length=500)),
        k=(int, Field(default=default_k, ge=1, le=MAX_K)),
    )

    async def handler(ctx: AgentContext, arguments: BaseModel) -> ToolResult:
        if ctx.retrieval is None:
            raise ToolError("Document search is not available for this run.")
        query = str(arguments.query)  # type: ignore[attr-defined]
        k = int(arguments.k)  # type: ignore[attr-defined]
        chunks = await ctx.retrieval.search(query, k)

        results: list[dict[str, object]] = []
        evidence: list[EvidenceRef] = []
        for index, chunk in enumerate(chunks, start=1):
            evidence_id = f"ev-doc-{index}"
            excerpt = chunk.text[:MAX_EXCERPT_CHARS]
            results.append(
                {
                    "evidence_id": evidence_id,
                    "title": chunk.title,
                    "version_no": chunk.version_no,
                    "section": chunk.section,
                    "page_number": chunk.page_number,
                    "excerpt": excerpt,
                }
            )
            description = f"{chunk.title} v{chunk.version_no}"
            if chunk.section:
                description += f", {chunk.section}"
            evidence.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    kind="document",
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    chunk_id=chunk.chunk_id,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    description=description,
                )
            )
        return ToolResult(data={"results": results}, evidence=evidence)

    return AgentTool(
        name="search_documents",
        description=(
            "Search the organization's SOPs and policy documents for passages relevant to the "
            "given query. Returns short excerpts with their source title, section and page. "
            "Numeric quality/capacity rules always come from the policy tables, never from this "
            "text."
        ),
        input_model=input_model,
        handler=handler,
    )
