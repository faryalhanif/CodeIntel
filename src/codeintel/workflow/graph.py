"""Orchestration. Uses LangGraph (state graph with parallel fan-out / fan-in) when installed, and
an equivalent thread-pool fallback otherwise, so the platform runs with zero optional dependencies.

        START -> plan -> [understanding | bug | security | testing | documentation] -> aggregate -> END
"""
from __future__ import annotations

import operator
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from ..agents.base import AgentContext
from ..agents.supervisor import AGENTS, build_report, plan
from ..models import AgentResult, AnalysisReport


class State(TypedDict, total=False):
    task: str
    requested: list[str] | None
    selected: list[str]
    results: Annotated[list[AgentResult], operator.add]     # reducer: parallel branches append
    report: AnalysisReport


def _run_fallback(ctx: AgentContext, task: str, requested: list[str] | None) -> AnalysisReport:
    selected = plan(task, requested)
    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as ex:
        results = list(ex.map(lambda n: AGENTS[n]().safe_run(ctx), selected))
    return build_report(ctx, task, {r.agent: r for r in results})


def _run_langgraph(ctx: AgentContext, task: str, requested: list[str] | None) -> AnalysisReport:
    from langgraph.graph import END, START, StateGraph

    def plan_node(state: State) -> dict:
        return {"selected": plan(state["task"], state.get("requested"))}

    def make_node(name: str):
        def node(state: State) -> dict:
            if name not in state["selected"]:
                return {"results": []}
            return {"results": [AGENTS[name]().safe_run(ctx)]}
        return node

    def aggregate_node(state: State) -> dict:
        order = {n: i for i, n in enumerate(AGENTS)}
        results = sorted(state["results"], key=lambda r: order[r.agent])
        return {"report": build_report(ctx, state["task"], {r.agent: r for r in results})}

    g = StateGraph(State)
    g.add_node("plan", plan_node)
    g.add_node("aggregate", aggregate_node)
    g.add_edge(START, "plan")
    for name in AGENTS:
        g.add_node(name, make_node(name))
        g.add_edge("plan", name)            # fan-out: all branches run in the same superstep
        g.add_edge(name, "aggregate")       # fan-in: aggregate waits for every branch
    g.add_edge("aggregate", END)
    out = g.compile().invoke({"task": task, "requested": requested, "results": []})
    return out["report"]


def run_analysis(ctx: AgentContext, task: str, requested: list[str] | None = None) -> AnalysisReport:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return _run_fallback(ctx, task, requested)
    return _run_langgraph(ctx, task, requested)
