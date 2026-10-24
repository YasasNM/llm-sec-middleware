"""Policy management API (blueprint 10.2, FR13, FR18, FR19)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile, status
from pydantic import BaseModel

from c3.api.auth import require_service_key
from c3.policy_service import PolicyNotFound, UploadRejected

OrgId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
DocId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]

router = APIRouter(
    prefix="/v1/orgs/{org_id}/policies",
    tags=["policies"],
    dependencies=[Depends(require_service_key)],
)


class PolicyDoc(BaseModel):
    doc_id: str
    org_id: str
    name: str
    filename: str
    doc_code: str
    category: str
    sha256: str
    size_bytes: int
    uploaded_at: str
    uploaded_by: str | None
    version: int
    status: str
    status_message: str | None
    clause_count: int


class PolicyStatus(BaseModel):
    org_id: str
    state: str
    documents: int
    clauses: int
    index_version: str
    retriever_version: str
    last_updated: str | None


def _svc(request: Request):
    return request.app.state.container.policies


def _doc(d: dict) -> PolicyDoc:
    return PolicyDoc(**{k: d[k] for k in PolicyDoc.model_fields})


def _read_cap(request: Request) -> int:
    lim = request.app.state.container.parser.limits
    return int(max(lim.max_pdf_mb * 1024 * 1024, lim.max_md_kb * 1024)) + 1


async def _read_upload(request: Request, file: UploadFile) -> bytes:
    # Read at most one byte over the largest allowed size, so a huge upload cannot fill memory
    return await file.read(_read_cap(request))


def _reject(exc: UploadRejected):
    raise HTTPException(exc.http_status, {"code": exc.code, "message": exc.message})


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PolicyDoc)
async def upload(
    org_id: OrgId,
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=200),
    category: str | None = Form(default=None, max_length=64),
    severity: str | None = Form(default=None, pattern="^(high|medium|low)$"),
    uploaded_by: str | None = Form(default=None, max_length=64),
):
    data = await _read_upload(request, file)
    try:
        doc = _svc(request).upload(
            org_id, file.filename or "policy", data, name=name, category=category,
            severity=severity, uploaded_by=uploaded_by,
        )
    except UploadRejected as exc:
        _reject(exc)
    return _doc(doc)


@router.get("", response_model=list[PolicyDoc])
def list_policies(org_id: OrgId, request: Request):
    return [_doc(d) for d in _svc(request).list_documents(org_id)]


@router.get("/status", response_model=PolicyStatus)
def policy_status(org_id: OrgId, request: Request):
    return _svc(request).status(org_id)


@router.get("/runs")
def index_runs(org_id: OrgId, request: Request, limit: int = 20):
    """What each upload or edit changed in the index (FR2): added, changed, removed, re-embedded."""
    rows = request.app.state.container.db.list_runs(org_id, max(1, min(limit, 100)))
    return [{k: r[k] for k in ("run_id", "doc_id", "path", "parse_ms", "embed_ms", "upsert_ms",
                               "added", "changed", "removed", "embedded", "index_version", "ok",
                               "message", "t0")} for r in rows]


@router.put("/{doc_id}", status_code=status.HTTP_202_ACCEPTED, response_model=PolicyDoc)
async def replace(
    org_id: OrgId,
    doc_id: DocId,
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=200),
    category: str | None = Form(default=None, max_length=64),
    severity: str | None = Form(default=None, pattern="^(high|medium|low)$"),
    uploaded_by: str | None = Form(default=None, max_length=64),
):
    data = await _read_upload(request, file)
    try:
        doc = _svc(request).replace(
            org_id, doc_id, file.filename or "policy", data, name=name, category=category,
            severity=severity, uploaded_by=uploaded_by,
        )
    except PolicyNotFound:
        raise HTTPException(404, "Policy document not found") from None
    except UploadRejected as exc:
        _reject(exc)
    return _doc(doc)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(org_id: OrgId, doc_id: DocId, request: Request):
    try:
        _svc(request).delete(org_id, doc_id)
    except PolicyNotFound:
        raise HTTPException(404, "Policy document not found") from None


@router.get("/{doc_id}", response_model=PolicyDoc)
def get_policy(org_id: OrgId, doc_id: DocId, request: Request):
    try:
        return _doc(_svc(request).get(org_id, doc_id))
    except PolicyNotFound:
        raise HTTPException(404, "Policy document not found") from None


@router.get("/{doc_id}/clauses")
def clauses(org_id: OrgId, doc_id: DocId, request: Request):
    try:
        return _svc(request).clauses(org_id, doc_id)
    except PolicyNotFound:
        raise HTTPException(404, "Policy document not found") from None
