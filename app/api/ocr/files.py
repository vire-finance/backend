"""Storage-aware implementations of the two existing private file routes.

Composed at registration so teammate-owned request modules remain untouched.
Request visibility is delegated to Context.request, as in FundRequestService.
"""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import anyio

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.request.repository import FundRequestRepository
from app.core.context import Ctx
from app.core.storage import materialize_document
from app.shared.utils import ensure

router = APIRouter(tags=["Fund Request"])


class ReceiptFileResponse(Response):
    """Keep the temporary file alive for sending, including Range requests.

ASGI finally cleanup also runs when the client disconnects or sending fails;
BackgroundTask alone would only clean up successfully completed responses.
"""
    def __init__(self, document, download=False):
        super().__init__()
        self.document = SimpleNamespace(**{name: getattr(document, name) for name in (
            "id", "user_id", "stored_filename", "storage_path", "file_size", "mime_type", "original_filename")})
        self.download = download

    async def __call__(self, scope, receive, send):
        manager = materialize_document(self.document)
        with anyio.CancelScope(shield=True):
            path = await run_in_threadpool(manager.__enter__)
        try:
            response = FileResponse(str(path), media_type=self.document.mime_type,
                filename=Path(self.document.original_filename.replace("\\", "/")).name,
                content_disposition_type="attachment" if self.download else "inline",
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
            # Do not hand an ephemeral path to a deferred sendfile extension.
            scope = {**scope, "extensions": {key: value for key, value in scope.get("extensions", {}).items()
                                              if key != "http.response.pathsend"}}
            await response(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(manager.__exit__, None, None, None)


def _document(ctx, row, document_id):
    doc = FundRequestRepository(ctx.db).document(document_id)
    ensure(doc is not None and doc.fund_request_id == row.id, "Dokumen tidak ditemukan.", 404)
    return doc


@router.get("/fund-requests/{request_id}/documents/{document_id}/file")
def document_file(request_id: UUID, document_id: UUID, ctx: Ctx, download: bool = False):
    row = ctx.request(request_id)
    return ReceiptFileResponse(_document(ctx, row, document_id), download)


@router.get("/fund-requests/{request_id}/receipt/file")
def receipt_file(request_id: UUID, ctx: Ctx):
    row = ctx.request(request_id)
    ensure(row.receipt_document_id is not None, "Receipt belum tersedia.", 404)
    return ReceiptFileResponse(_document(ctx, row, row.receipt_document_id))


def with_storage_file_routes(request_router):
    replacements = {route.path: route for route in router.routes}
    matched = set()
    routes = []
    for route in request_router.routes:
        if route.path in replacements and route.methods == {"GET"}:
            routes.append(replacements[route.path])
            matched.add(route.path)
        else:
            routes.append(route)
    if matched != replacements.keys():
        raise RuntimeError("Receipt route composition no longer matches the request API")
    return APIRouter(routes=routes)
