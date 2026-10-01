from mongoengine import Document, StringField


class Report(Document):
    content = StringField()


def generate(state):
    return {"content": f"Report: {state['seed']}", "attempts": 0}


def validate(state):
    return {"valid": bool(state["content"])}


def route(state):
    return "done" if state["valid"] or state["attempts"] >= 2 else "retry"


def repair(state):
    return {"content": "Repaired report", "attempts": state["attempts"] + 1}


def persist(state):
    document = Report(content=state["content"])
    document.save()
    return {"report_id": str(document.id)}


def initialize_empty():
    document = Report(content="")
    document.save()
    return str(document.id)
