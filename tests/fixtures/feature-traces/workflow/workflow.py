from langgraph.graph import StateGraph

from .nodes import generate, persist, repair, route, validate


def build_workflow():
    workflow = StateGraph(dict)
    workflow.add_node("generate", generate)
    workflow.add_node("validate", validate)
    workflow.add_node("repair", repair)
    workflow.add_node("persist", persist)
    workflow.set_entry_point("generate")
    workflow.add_edge("generate", "validate")
    workflow.add_conditional_edges("validate", route, {"retry": "repair", "done": "persist"})
    workflow.add_edge("repair", "validate")
    return workflow.compile()
