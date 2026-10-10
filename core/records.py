import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MacroValues(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    housing_starts_index: float = Field(ge=0)
    lumber_price_index: float = Field(ge=0)
    mortgage_rate: float = Field(ge=0)
    season_factor: float = Field(ge=0)


class MacroRecord(MacroValues):
    week: dt.date

    @field_validator("week")
    @classmethod
    def validate_week(cls, value: dt.date) -> dt.date:
        if value.weekday() != 0:
            raise ValueError("Неделя должна начинаться с понедельника")
        return value


class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    date: dt.date
    region: str = Field(min_length=1, max_length=120)
    product_category: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=120)
    channel: Literal["pro_dealer", "big_box_diy"]
    customer_type: Literal["homebuilder", "contractor", "remodeler", "homeowner_diy"]
    units: int = Field(gt=0, le=2147483647)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: dt.date) -> dt.date:
        if value.weekday() != 0:
            raise ValueError("Дата продажи должна обозначать понедельник недели")
        return value

    @field_validator("units", "unit_price", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Ожидается число, а не логическое значение")
        return value

    @model_validator(mode="after")
    def validate_revenue(self):
        if self.units * self.unit_price > Decimal("99999999999999.99"):
            raise ValueError("Выручка превышает допустимое значение Numeric(16, 2)")
        return self

    def to_row(self) -> dict:
        values = self.model_dump()
        values.update(
            year=self.date.year,
            month=self.date.month,
            week_of_year=self.date.isocalendar()[1],
            unit_price=self.unit_price.quantize(Decimal("0.01")),
            revenue=(self.units * self.unit_price).quantize(Decimal("0.01")),
        )
        return values
