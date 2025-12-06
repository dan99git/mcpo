from datetime import datetime
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "generation": int(datetime.now().timestamp())}
