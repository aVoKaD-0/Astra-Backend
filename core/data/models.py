from sqlalchemy import Column, Date, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class BuildingMaterialTransaction(Base):
    __tablename__ = "building_materials_transactions"

    id = Column(Integer, primary_key=True)
    date = Column(Date, ForeignKey("macro_drivers_weekly.week"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    week_of_year = Column(Integer, nullable=False)
    region = Column(String, nullable=False)
    product_category = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    customer_type = Column(String, nullable=False)
    units = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    revenue = Column(Numeric(16, 2), nullable=False)

    macro_week = relationship(
        "MacroDriverWeekly",
        back_populates="transactions",
    )


class MacroDriverWeekly(Base):
    __tablename__ = "macro_drivers_weekly"

    week = Column(Date, nullable=False, primary_key=True)
    housing_starts_index = Column(Float, nullable=False)
    lumber_price_index = Column(Float, nullable=False)
    mortgage_rate = Column(Float, nullable=False)
    season_factor = Column(Float, nullable=False)

    transactions = relationship(
        "BuildingMaterialTransaction",
        back_populates="macro_week",
    )


class ImportedTransaction(Base):
    __tablename__ = "imported_transactions"

    fingerprint = Column(String(64), primary_key=True)
    occurrence = Column(Integer, primary_key=True)
    transaction_id = Column(
        Integer,
        ForeignKey("building_materials_transactions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
