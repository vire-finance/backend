from uuid import UUID

from fastapi import APIRouter, Response

from app.api.pocket.schema import PocketCreate, PocketUpdate
from app.api.pocket.service import PocketService
from app.core.context import Ctx

router = APIRouter(tags=["Pocket"])

@router.get("/employees")
def list_employees(ctx: Ctx):
    return PocketService(ctx).employees()

@router.get("/pockets")
def list_pockets(ctx: Ctx):
    return PocketService(ctx).list()

@router.post("/pockets", status_code=201)
def create_pocket(payload: PocketCreate, ctx: Ctx):
    return PocketService(ctx).create(payload)

@router.get("/pockets/{pocket_id}")
def detail_pocket(pocket_id: UUID, ctx: Ctx):
    return PocketService(ctx).detail(pocket_id)

@router.patch("/pockets/{pocket_id}")
def update_pocket(pocket_id: UUID, payload: PocketUpdate, ctx: Ctx):
    return PocketService(ctx).update(pocket_id, payload)

@router.get("/pockets/{pocket_id}/employees")
def pocket_employees(pocket_id: UUID, ctx: Ctx):
    return PocketService(ctx).employees(pocket_id)

@router.put("/pockets/{pocket_id}/employees/{employee_id}")
def grant_access(pocket_id: UUID, employee_id: UUID, ctx: Ctx):
    return PocketService(ctx).access(pocket_id, employee_id, True)

@router.delete("/pockets/{pocket_id}/employees/{employee_id}")
def revoke_access(pocket_id: UUID, employee_id: UUID, ctx: Ctx):
    return PocketService(ctx).access(pocket_id, employee_id, False)

@router.get("/pockets/{pocket_id}/analysis")
def analysis(pocket_id: UUID, ctx: Ctx):
    return PocketService(ctx).analysis(pocket_id)

@router.get("/pockets/{pocket_id}/report.csv")
def report(pocket_id: UUID, ctx: Ctx):
    return Response(
        content=PocketService(ctx).report(pocket_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket-{pocket_id}.csv"'
            ),
            "Cache-Control": "private, no-store",
        },
    )