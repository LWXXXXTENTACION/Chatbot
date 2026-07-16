"""Authenticated APIs for run replay, version comparison, and evaluation."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import get_db
from app.database.models import User
from app.database.observability import (
    aggregate_versions,
    list_run_traces,
    update_run_evaluation,
)
from app.middleware.auth import get_current_user
from app.schemas.observability import RunEvaluationUpdate


router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/overview")
async def observability_overview(
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    traces = await list_run_traces(db, str(current_user.id), limit=limit)
    return {"versions": aggregate_versions(traces), "runs": traces}


@router.patch("/runs/{run_id}/evaluation")
async def evaluate_run(
    run_id: str,
    body: RunEvaluationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trace = await update_run_evaluation(
        db,
        str(current_user.id),
        run_id,
        passed=body.passed,
        note=body.note,
        case_id=body.case_id,
    )
    if trace is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return trace
