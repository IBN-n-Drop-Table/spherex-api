from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.reports import CitizenReport, CitizenReportCreate, Classification
from app.schemas.sky_patch import ErrorResponse
from app.services.reports import ReportService, get_report_service

router = APIRouter(prefix="/reports", tags=["citizen-reports"])

Service = Annotated[ReportService, Depends(get_report_service)]

REPORT_NOT_FOUND = "Report not found."


@router.post(
    "",
    response_model=CitizenReport,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a citizen discovery report",
)
def submit_report(report: CitizenReportCreate, service: Service) -> CitizenReport:
    return service.submit(report)


@router.get(
    "",
    response_model=list[CitizenReport],
    summary="Community feed: recent discoveries, newest first",
)
def community_feed(
    service: Service,
    target_name: Annotated[
        str | None,
        Query(description="e.g. 'WISE 0855' or 'WISE_0855'", examples=["WISE_0855"]),
    ] = None,
    classification: Annotated[Classification | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CitizenReport]:
    return service.feed(limit=limit, target_name=target_name, classification=classification)


@router.post(
    "/{report_id}/verify",
    response_model=CitizenReport,
    summary="Peer verification: confirm another citizen's report (+1)",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
def verify_report(report_id: UUID, service: Service) -> CitizenReport:
    report = service.verify(report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=REPORT_NOT_FOUND)
    return report
