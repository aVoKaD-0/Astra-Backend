from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement

from core.data import BuildingMaterialTransaction, MacroDriverWeekly


class MissingMacroWeekError(ValueError):
    def __init__(self, week: date) -> None:
        self.week = week
        super().__init__(f"Сначала загрузите недельные показатели за {week.isoformat()}.")


@dataclass(frozen=True)
class TransactionFilters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    region: Optional[str] = None
    product_category: Optional[str] = None
    sku: Optional[str] = None
    channel: Optional[str] = None
    customer_type: Optional[str] = None

    def __post_init__(self) -> None:
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("Начало периода не может быть позже конца.")


@dataclass(frozen=True)
class TransactionTotals:
    transaction_count: int
    total_units: int
    total_revenue: Decimal


def get_macro_week(session: Session, week: date) -> Optional[MacroDriverWeekly]:
    return session.get(MacroDriverWeekly, week)


def upsert_macro_week(
    session: Session,
    *,
    week: date,
    housing_starts_index: float,
    lumber_price_index: float,
    mortgage_rate: float,
    season_factor: float,
) -> MacroDriverWeekly:
    values = {
        "week": week,
        "housing_starts_index": housing_starts_index,
        "lumber_price_index": lumber_price_index,
        "mortgage_rate": mortgage_rate,
        "season_factor": season_factor,
    }
    statement = insert(MacroDriverWeekly).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[MacroDriverWeekly.week],
        set_={name: statement.excluded[name] for name in values if name != "week"},
    ).returning(MacroDriverWeekly)

    return session.scalars(statement, execution_options={"populate_existing": True}).one()


def add_transaction(
    session: Session,
    transaction: BuildingMaterialTransaction,
) -> BuildingMaterialTransaction:
    if get_macro_week(session, transaction.date) is None:
        raise MissingMacroWeekError(transaction.date)

    session.add(transaction)
    session.flush()
    return transaction


def get_transaction(session: Session, transaction_id: int) -> Optional[BuildingMaterialTransaction]:
    statement = (
        select(BuildingMaterialTransaction)
        .where(BuildingMaterialTransaction.id == transaction_id)
        .options(joinedload(BuildingMaterialTransaction.macro_week))
    )
    return session.scalars(statement).one_or_none()


def _filter_conditions(filters: TransactionFilters) -> list[ColumnElement[bool]]:
    conditions = []
    if filters.date_from is not None:
        conditions.append(BuildingMaterialTransaction.date >= filters.date_from)
    if filters.date_to is not None:
        conditions.append(BuildingMaterialTransaction.date <= filters.date_to)

    for name in ("region", "product_category", "sku", "channel", "customer_type"):
        value = getattr(filters, name)
        if value is not None:
            conditions.append(getattr(BuildingMaterialTransaction, name) == value)
    return conditions


def list_transactions(
    session: Session,
    filters: Optional[TransactionFilters] = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[BuildingMaterialTransaction]:
    if not 1 <= limit <= 1000:
        raise ValueError("Размер страницы должен быть от 1 до 1000.")
    if offset < 0:
        raise ValueError("Смещение не может быть отрицательным.")

    statement = (
        select(BuildingMaterialTransaction)
        .where(*_filter_conditions(filters or TransactionFilters()))
        .options(joinedload(BuildingMaterialTransaction.macro_week))
        .order_by(BuildingMaterialTransaction.date, BuildingMaterialTransaction.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement))


def aggregate_transactions(
    session: Session, filters: Optional[TransactionFilters] = None
) -> TransactionTotals:
    statement = select(
        func.count(BuildingMaterialTransaction.id).label("transaction_count"),
        func.coalesce(func.sum(BuildingMaterialTransaction.units), 0).label("total_units"),
        func.coalesce(func.sum(BuildingMaterialTransaction.revenue), Decimal("0.00")).label(
            "total_revenue"
        ),
    ).where(*_filter_conditions(filters or TransactionFilters()))
    row = session.execute(statement).one()
    return TransactionTotals(
        transaction_count=row.transaction_count,
        total_units=row.total_units,
        total_revenue=row.total_revenue,
    )


def list_macro_weeks(session: Session, *, limit: int = 100, offset: int = 0):
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("Недопустимые параметры страницы.")
    statement = select(MacroDriverWeekly).order_by(MacroDriverWeekly.week)
    return list(session.scalars(statement.limit(limit).offset(offset)))


def aggregate_transactions_by(
    session: Session, filters: TransactionFilters, group_by: str
) -> list[dict]:
    allowed = {"region", "product_category", "channel", "customer_type", "date"}
    if group_by not in allowed:
        raise ValueError("Недопустимое поле группировки.")
    column = getattr(BuildingMaterialTransaction, group_by)
    statement = (
        select(
            column.label("value"),
            func.count(BuildingMaterialTransaction.id).label("transaction_count"),
            func.sum(BuildingMaterialTransaction.units).label("total_units"),
            func.sum(BuildingMaterialTransaction.revenue).label("total_revenue"),
        )
        .where(*_filter_conditions(filters))
        .group_by(column)
        .order_by(column)
    )
    return [dict(row, value=str(row["value"])) for row in session.execute(statement).mappings()]
