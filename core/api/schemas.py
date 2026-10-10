import datetime as dt
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class MacroWeekRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    week: dt.date
    housing_starts_index: float
    lumber_price_index: float
    mortgage_rate: float
    season_factor: float


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    year: int
    month: int
    week_of_year: int
    region: str
    product_category: str
    sku: str
    channel: str
    customer_type: str
    units: int
    unit_price: Decimal
    revenue: Decimal
    macro_week: MacroWeekRead


class TransactionPage(BaseModel):
    items: list[TransactionRead]
    limit: int
    offset: int


class GroupTotals(BaseModel):
    value: str
    transaction_count: int
    total_units: int
    total_revenue: Decimal


class AggregateRead(BaseModel):
    transaction_count: int
    total_units: int
    total_revenue: Decimal
    group_by: Optional[str] = None
    groups: list[GroupTotals]
