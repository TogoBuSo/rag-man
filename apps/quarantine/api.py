from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.quarantine.schemas import QuarantineResolveIn, QuarantineReviewOut
from apps.quarantine.services import list_pending_reviews, resolve_quarantine_review

router = Router()


@router.get("/quarantine", response=list[QuarantineReviewOut])
def list_quarantine_endpoint(request: HttpRequest) -> list[QuarantineReviewOut]:
    return list_pending_reviews(request.tenant)  # type: ignore[attr-defined]


@router.post("/quarantine/{review_id}/resolve", response=QuarantineReviewOut)
def resolve_quarantine_endpoint(
    request: HttpRequest,
    review_id: UUID,
    payload: QuarantineResolveIn,
) -> QuarantineReviewOut:
    return resolve_quarantine_review(request.tenant, review_id, payload)  # type: ignore[attr-defined]
