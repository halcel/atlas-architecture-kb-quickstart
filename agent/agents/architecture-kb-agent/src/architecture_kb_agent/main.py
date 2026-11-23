"""Architecture assistant: answers from the Confluence knowledge base in MongoDB Atlas."""

from __future__ import annotations

import json
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from agent_engine_sdk_langgraph import App

from architecture_kb_agent import knowledge_base as kb
from architecture_kb_agent.llm import build_llm

load_dotenv()

SYSTEM_PROMPT = """You are an architecture assistant for an enterprise IT team.
Answer only from the knowledge base. Call search_knowledge_base before answering, and search again with
different wording if the first results do not cover the question.
Cite every claim as (Page title, section). If the knowledge base does not answer the question, say so
and suggest which page or decision record is missing. Answer in the language of the question."""

app = App(app_name="Architecture KB Agent")


@app.tool(is_local=False, timeout=60)
def search_knowledge_base(query: str, limit: int = 5, space: str | None = None) -> str:
    """Semantic search over Confluence pages (architecture decisions, guidelines).
    query: the question or keywords in natural language. space: optional Confluence space key filter."""
    return json.dumps(kb.search(query, limit=limit, space=space), ensure_ascii=False)


@app.tool(is_local=False, timeout=60)
def list_pages() -> str:
    """Which Confluence pages are in the knowledge base (title, space, url, chunk count)."""
    return json.dumps(kb.pages(), ensure_ascii=False)


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    llm = app.llm(build_llm())
    llm_with_tools = llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: AgentState) -> dict:
        history = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
        return {"messages": [llm_with_tools.invoke([SystemMessage(content=SYSTEM_PROMPT), *history])]}

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "end"

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(app.get_tools()))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
