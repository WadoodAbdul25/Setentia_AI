from .workflow import build_workflow


def run_workflow(seed: str):
    workflow = build_workflow()
    return workflow.invoke({"seed": seed})
