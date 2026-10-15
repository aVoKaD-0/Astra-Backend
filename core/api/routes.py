from dataclasses import asdict
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from core import MacroValues, TransactionInput
from core.api import AggregateRead, MacroWeekRead, TransactionPage, TransactionRead
from core.api.dependencies import get_filters, get_session
from core.data import BuildingMaterialTransaction, queries

router = APIRouter()


@router.get("/health", tags=["Service"])
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.put("/macro-weeks/{week}", response_model=MacroWeekRead, tags=["Macro weeks"])
def save_macro_week(week: date, values: MacroValues, session: Session = Depends(get_session)):
    if week.weekday() != 0:
        raise HTTPException(status_code=422, detail="Неделя должна начинаться с понедельника.")
    with session.begin():
        record = queries.upsert_macro_week(session, week=week, **values.model_dump())
        result = MacroWeekRead.model_validate(record)
    return result


@router.get("/macro-weeks", response_model=list[MacroWeekRead], tags=["Macro weeks"])
def read_macro_weeks(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    return queries.list_macro_weeks(session, limit=limit, offset=offset)


@router.get("/macro-weeks/{week}", response_model=MacroWeekRead, tags=["Macro weeks"])
def read_macro_week(week: date, session: Session = Depends(get_session)):
    record = queries.get_macro_week(session, week)
    if record is None:
        raise HTTPException(status_code=404, detail="Недельные показатели не найдены.")
    return record


@router.post(
    "/transactions", response_model=TransactionRead, status_code=201, tags=["Transactions"]
)
def create_transaction(values: TransactionInput, session: Session = Depends(get_session)):
    with session.begin():
        record = queries.add_transaction(session, BuildingMaterialTransaction(**values.to_row()))
        result = TransactionRead.model_validate(record)
    return result


@router.get("/transactions", response_model=TransactionPage, tags=["Transactions"])
def read_transactions(
    filters: queries.TransactionFilters = Depends(get_filters),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    records = queries.list_transactions(session, filters, limit=limit, offset=offset)
    return TransactionPage(items=records, limit=limit, offset=offset)


@router.get("/transactions/aggregate", response_model=AggregateRead, tags=["Transactions"])
def read_totals(
    filters: queries.TransactionFilters = Depends(get_filters),
    group_by: Optional[
        Literal["region", "product_category", "channel", "customer_type", "date"]
    ] = None,
    session: Session = Depends(get_session),
):
    totals = queries.aggregate_transactions(session, filters)
    groups = queries.aggregate_transactions_by(session, filters, group_by) if group_by else []
    return AggregateRead(**asdict(totals), group_by=group_by, groups=groups)


@router.get("/transactions/{transaction_id}", response_model=TransactionRead, tags=["Transactions"])
def read_transaction(
    transaction_id: int = Path(..., ge=1), session: Session = Depends(get_session)
):
    record = queries.get_transaction(session, transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Продажа не найдена.")
    return record
