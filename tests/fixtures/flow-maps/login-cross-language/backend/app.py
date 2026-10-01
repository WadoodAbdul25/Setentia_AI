from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class Credentials(BaseModel):
    email: str
    password: str


def verify_credentials(credentials: Credentials) -> bool:
    return credentials.email == "developer@example.com" and credentials.password == "sentia"


def issue_session() -> dict[str, str]:
    return {"token": "fixture-token"}


@app.post("/api/session")
async def create_session(credentials: Credentials) -> dict[str, str]:
    if not verify_credentials(credentials):
        raise ValueError("Invalid credentials")
    return issue_session()
