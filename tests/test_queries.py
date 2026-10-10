import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.data import BuildingMaterialTransaction, MacroDriverWeekly
from core.data.queries import (
    MissingMacroWeekError,
    TransactionFilters,
    add_transaction,
    aggregate_transactions,
    get_macro_week,
    get_transaction,
    list_transactions,
    upsert_macro_week,
)
from tests.support import PostgreSQLTest

WEEK_ONE = date(2019, 1, 7)
WEEK_TWO = date(2019, 1, 14)


class QueriesTest(PostgreSQLTest):
    def save_week(self, week=WEEK_ONE, mortgage_rate=3.95):
        return upsert_macro_week(
            self.session,
            week=week,
            housing_starts_index=96.4,
            lumber_price_index=388.0,
            mortgage_rate=mortgage_rate,
            season_factor=0.581,
        )

    def sale(
        self,
        week=WEEK_ONE,
        region="Pacific",
        category="lumber",
        sku="LUM-001",
        channel="pro_dealer",
        customer_type="contractor",
        units=3,
        price="0.10",
    ):
        unit_price = Decimal(price)
        return BuildingMaterialTransaction(
            date=week,
            year=week.year,
            month=week.month,
            week_of_year=week.isocalendar()[1],
            region=region,
            product_category=category,
            sku=sku,
            channel=channel,
            customer_type=customer_type,
            units=units,
            unit_price=unit_price,
            revenue=unit_price * units,
        )

    def seed_sales(self):
        self.save_week(WEEK_ONE)
        self.save_week(WEEK_TWO)
        sales = [
            self.sale(),
            self.sale(
                category="roofing",
                sku="ROO-001",
                channel="big_box_diy",
                customer_type="homeowner_diy",
                units=2,
                price="10.00",
            ),
            self.sale(WEEK_TWO, region="South_Atlantic", units=4, price="2.25"),
            self.sale(
                WEEK_TWO,
                sku="LUM-002",
                channel="big_box_diy",
                customer_type="remodeler",
                units=5,
                price="1.01",
            ),
        ]
        for sale in sales:
            add_transaction(self.session, sale)
        return sales

    def test_week_upsert_updates_without_duplicate_or_stale_object(self):
        original = self.save_week()
        updated = self.save_week(mortgage_rate=4.5)
        self.assertIs(original, updated)
        self.assertEqual(original.mortgage_rate, 4.5)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MacroDriverWeekly)),
            1,
        )

    def test_sale_requires_existing_week(self):
        with self.assertRaises(MissingMacroWeekError) as caught:
            add_transaction(self.session, self.sale())
        self.assertEqual(caught.exception.week, WEEK_ONE)
        self.assertEqual(aggregate_transactions(self.session).transaction_count, 0)

    def test_sale_preserves_decimal_amount_and_loads_macro_week(self):
        self.save_week()
        saved = add_transaction(self.session, self.sale())
        transaction_id = saved.id
        self.assertIsNotNone(transaction_id)
        self.session.expunge_all()

        loaded = get_transaction(self.session, transaction_id)
        self.assertEqual(loaded.unit_price, Decimal("0.10"))
        self.assertEqual(loaded.revenue, Decimal("0.30"))
        self.assertEqual(loaded.macro_week.week, WEEK_ONE)

    def test_missing_objects_return_none(self):
        self.assertIsNone(get_macro_week(self.session, WEEK_ONE))
        self.assertIsNone(get_transaction(self.session, 1))

    def test_filters_and_inclusive_date_bounds(self):
        sales = self.seed_sales()
        cases = [
            (TransactionFilters(date_from=WEEK_ONE, date_to=WEEK_ONE), [0, 1]),
            (TransactionFilters(date_from=WEEK_TWO), [2, 3]),
            (TransactionFilters(region="Pacific"), [0, 1, 3]),
            (TransactionFilters(product_category="roofing"), [1]),
            (TransactionFilters(sku="LUM-001"), [0, 2]),
            (TransactionFilters(channel="big_box_diy"), [1, 3]),
            (TransactionFilters(customer_type="remodeler"), [3]),
            (TransactionFilters(region="Pacific", product_category="lumber"), [0, 3]),
            (TransactionFilters(region="' OR 1=1 --"), []),
        ]
        for filters, positions in cases:
            with self.subTest(filters=filters):
                actual = list_transactions(self.session, filters)
                self.assertEqual([sale.id for sale in actual], [sales[i].id for i in positions])

    def test_pagination_is_stable_and_does_not_limit_totals(self):
        sales = self.seed_sales()
        page = list_transactions(self.session, limit=2, offset=1)
        self.assertEqual([sale.id for sale in page], [sales[1].id, sales[2].id])
        totals = aggregate_transactions(self.session)
        self.assertEqual(totals.transaction_count, 4)
        self.assertEqual(totals.total_units, 14)
        self.assertEqual(totals.total_revenue, Decimal("34.35"))

    def test_aggregation_uses_the_same_filters(self):
        self.seed_sales()
        totals = aggregate_transactions(
            self.session,
            TransactionFilters(region="Pacific", product_category="lumber"),
        )
        self.assertEqual(totals.transaction_count, 2)
        self.assertEqual(totals.total_units, 8)
        self.assertEqual(totals.total_revenue, Decimal("5.35"))

    def test_empty_aggregation_returns_typed_zeros(self):
        self.seed_sales()
        totals = aggregate_transactions(self.session, TransactionFilters(region="unknown"))
        self.assertEqual(totals.transaction_count, 0)
        self.assertEqual(totals.total_units, 0)
        self.assertEqual(totals.total_revenue, Decimal("0.00"))
        self.assertIsInstance(totals.total_revenue, Decimal)

    def test_caller_can_rollback_all_writes(self):
        self.save_week()
        add_transaction(self.session, self.sale())
        self.session.rollback()
        self.assertIsNone(get_macro_week(self.session, WEEK_ONE))
        self.assertEqual(aggregate_transactions(self.session).transaction_count, 0)

    def test_database_fk_also_rejects_dangling_sale(self):
        with self.assertRaises(IntegrityError):
            with self.session.begin_nested():
                self.session.add(self.sale())
                self.session.flush()

    def test_invalid_pagination_and_date_range(self):
        with self.assertRaises(ValueError):
            TransactionFilters(date_from=WEEK_TWO, date_to=WEEK_ONE)
        for arguments in [{"limit": 0}, {"limit": 1001}, {"offset": -1}]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                list_transactions(self.session, **arguments)


if __name__ == "__main__":
    unittest.main()
