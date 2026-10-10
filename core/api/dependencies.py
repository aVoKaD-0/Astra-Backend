from datetime import date
from typing import Optional

from fastapi import HTTPException, Query

from core.data.database import SessionLocal
from core.data.queries import TransactionFilters


def get_session():
    with SessionLocal() as session:
        yield session


def get_filters(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    region: Optional[str] = Query(None, min_length=1, max_length=120),
    product_category: Optional[str] = Query(None, min_length=1, max_length=120),
    sku: Optional[str] = Query(None, min_length=1, max_length=120),
    channel: Optional[str] = Query(None, min_length=1, max_length=120),
    customer_type: Optional[str] = Query(None, min_length=1, max_length=120),
) -> TransactionFilters:
    try:
        return TransactionFilters(
            date_from=date_from,
            date_to=date_to,
            region=region,
            product_category=product_category,
            sku=sku,
            channel=channel,
            customer_type=customer_type,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
