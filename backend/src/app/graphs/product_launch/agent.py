"""装配并导出编译图变量 `graph`。

langgraph.json 指向本文件：./src/app/graphs/product_launch/agent.py:graph
单一定义、两种运行时（v1.4 §2.3 规则4）：`langgraph dev` 与 FastAPI 主运行时
都 import 这同一份定义；禁止在任何地方出现第二份图装配。

主干顺序遵循 PRD §7.13：go/no-go 闸门在 研究+利润+供应商 之后、Listing 生成之前；
两条回路：research 自深化（≤MAX_RESEARCH_ROUNDS）、critic 重写（≤MAX_CRITIQUE_ROUNDS）。
"""

from langgraph.graph import END, START, StateGraph

from app.graphs.product_launch import edges, nodes
from app.graphs.product_launch.state import ProductLaunchState


def build_graph():
    g = StateGraph(ProductLaunchState)

    g.add_node("planner", nodes.node_planner)
    g.add_node("research", nodes.node_research)
    g.add_node("profit", nodes.node_profit)
    g.add_node("supplier", nodes.node_supplier)
    g.add_node("decision_gate", nodes.node_decision_gate)
    g.add_node("listing", nodes.node_listing)
    g.add_node("critic", nodes.node_critic)
    g.add_node("approval_check", nodes.node_approval_check)
    g.add_node("publish", nodes.node_publish)
    g.add_node("ops", nodes.node_ops)
    g.add_node("support", nodes.node_support)
    g.add_node("retrospective", nodes.node_retrospective)
    g.add_node("halted", nodes.node_halted)

    g.add_edge(START, "planner")
    g.add_edge("planner", "research")
    g.add_conditional_edges(
        "research", edges.route_after_research, {"deepen": "research", "gate": "profit"}
    )
    g.add_edge("profit", "supplier")
    g.add_edge("supplier", "decision_gate")
    g.add_conditional_edges(
        "decision_gate", edges.route_after_gate, {"proceed": "listing", "halt": "halted"}
    )
    g.add_edge("listing", "critic")
    g.add_conditional_edges(
        "critic", edges.route_after_critic, {"rewrite": "listing", "approve": "approval_check"}
    )
    g.add_conditional_edges(
        "approval_check", edges.route_after_approval, {"publish": "publish", "halt": "halted"}
    )
    g.add_edge("publish", "ops")
    g.add_edge("ops", "support")
    g.add_edge("support", "retrospective")
    g.add_edge("retrospective", END)
    g.add_edge("halted", END)
    return g.compile()


graph = build_graph()
