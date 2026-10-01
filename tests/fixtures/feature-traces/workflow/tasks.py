from celery import shared_task

from .service import run_workflow


@shared_task
def generate_report(seed: str):
    return run_workflow(seed)
