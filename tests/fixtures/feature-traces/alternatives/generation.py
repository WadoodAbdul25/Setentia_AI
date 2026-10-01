def submit_seed(seed):
    sections = generate_sections(seed)
    record_generation_event(sections)
    return save_draft(sections)


def generate_sections(seed):
    return {"overview": seed}


def record_generation_event(sections):
    print("generation complete", len(sections))


def save_draft(sections):
    return {"status": "draft", "sections": sections}


def generate_sharing_email(existing_document, recipient):
    return {"recipient": recipient, "body": existing_document["sections"]}


def generate_prototype(blueprint):
    return {"App.jsx": blueprint}


def finalize_document(document):
    document["status"] = "finalized"
    return document
