from fastapi import APIRouter

from .tasks import generate_report

router = APIRouter(prefix="/reports")


@router.post("/generate")
def submit(seed: str):
    return generate_report.delay(seed)
