import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select

from core.data import ImportedTransaction, MacroDriverWeekly
from core.data.queries import aggregate_transactions, get_macro_week, list_transactions
from core.import_csv import CSVImportError, import_files
from tests.support import PostgreSQLTest

MACRO = {
    "week": "2019-01-07",
    "housing_starts_index": "96.4",
    "lumber_price_index": "388.0",
    "mortgage_rate": "3.95",
    "season_factor": "0.581",
}
SALE = {
    "date": "2019-01-07",
    "year": "2019",
    "month": "1",
    "week_of_year": "2",
    "region": "Pacific",
    "product_category": "lumber",
    "sku": "LUM-001",
    "channel": "pro_dealer",
    "customer_type": "contractor",
    "units": "3",
    "unit_price": "0.10",
    "revenue": "0.30",
    "housing_starts_index": "96.4",
    "lumber_price_index": "388.0",
    "mortgage_rate": "3.95",
}


class ImportTest(PostgreSQLTest):
    def setUp(self):
        super().setUp()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.macros = self.write_csv("macros.csv", [MACRO])
        self.sales = self.write_csv("sales.csv", [SALE])

    def write_csv(self, name, rows):
        path = self.directory / name
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_combined_import_orders_weeks_before_sales(self):
        with self.session.begin():
            stats = import_files(self.session, macro_path=self.macros, transactions_path=self.sales)
        self.assertEqual((stats.macro_rows, stats.inserted_transactions), (1, 1))
        sale = list_transactions(self.session)[0]
        self.assertEqual(sale.revenue, Decimal("0.30"))
        self.assertEqual(sale.macro_week.week, date(2019, 1, 7))

    def test_repeat_overlapping_exports_and_identical_rows_across_batches(self):
        original_rows = [SALE, SALE, {**SALE, "sku": "LUM-002"}]
        self.sales = self.write_csv("sales.csv", original_rows)
        with self.session.begin():
            first = import_files(
                self.session, macro_path=self.macros, transactions_path=self.sales, batch_size=1
            )
        self.assertEqual(first.inserted_transactions, 3)
        with self.session.begin():
            repeat = import_files(self.session, transactions_path=self.sales, batch_size=2)
        self.assertEqual((repeat.inserted_transactions, repeat.skipped_transactions), (0, 3))

        new_rows = [original_rows[2], SALE, {**SALE, "sku": "LUM-003"}, SALE, SALE]
        updated = self.write_csv("next.csv", new_rows)
        with self.session.begin():
            incremental = import_files(self.session, transactions_path=updated, batch_size=2)
        self.assertEqual(
            (incremental.inserted_transactions, incremental.skipped_transactions), (2, 3)
        )
        totals = aggregate_transactions(self.session)
        self.assertEqual(totals.transaction_count, 5)
        self.assertEqual(totals.total_revenue, Decimal("1.50"))
        ledger_count = self.session.scalar(select(func.count()).select_from(ImportedTransaction))
        self.assertEqual(ledger_count, 5)
        links = list(
            self.session.scalars(
                select(ImportedTransaction.transaction_id).order_by(
                    ImportedTransaction.transaction_id
                )
            )
        )
        self.assertEqual(links, [sale.id for sale in list_transactions(self.session)])

    def test_numeric_formatting_does_not_create_duplicates(self):
        with self.session.begin():
            import_files(self.session, macro_path=self.macros, transactions_path=self.sales)
        formatted = self.write_csv(
            "formatted.csv", [{**SALE, "unit_price": "0.1", "units": "03", "revenue": "0.300"}]
        )
        with self.session.begin():
            stats = import_files(self.session, transactions_path=formatted)
        self.assertEqual((stats.inserted_transactions, stats.skipped_transactions), (0, 1))

    def test_week_upsert_updates_existing_value(self):
        with self.session.begin():
            import_files(self.session, macro_path=self.macros)
        updated = self.write_csv("updated.csv", [{**MACRO, "mortgage_rate": "4.5"}])
        with self.session.begin():
            import_files(self.session, macro_path=updated)
        self.assertEqual(get_macro_week(self.session, date(2019, 1, 7)).mortgage_rate, 4.5)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MacroDriverWeekly)), 1
        )

    def test_missing_week_names_the_source_line(self):
        with self.assertRaisesRegex(CSVImportError, "sales.csv, строка 2.*2019-01-07"):
            with self.session.begin():
                import_files(self.session, transactions_path=self.sales)
        self.assertEqual(aggregate_transactions(self.session).transaction_count, 0)

    def test_failed_later_batch_rolls_back_every_table(self):
        broken = self.write_csv("broken.csv", [SALE, {**SALE, "units": "-1"}])
        with self.assertRaisesRegex(CSVImportError, "broken.csv, строка 3"):
            with self.session.begin():
                import_files(
                    self.session, macro_path=self.macros, transactions_path=broken, batch_size=1
                )
        self.assertEqual(aggregate_transactions(self.session).transaction_count, 0)
        self.assertIsNone(get_macro_week(self.session, date(2019, 1, 7)))
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(ImportedTransaction)), 0
        )

    def test_rejects_inconsistent_derived_or_macro_values(self):
        cases = [
            {"year": "2020"},
            {"month": "2"},
            {"week_of_year": "1"},
            {"revenue": "3.00"},
            {"mortgage_rate": "4.5"},
            {"lumber_price_index": "NaN"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                invalid = self.write_csv("invalid.csv", [{**SALE, **changes}])
                with self.assertRaises(CSVImportError), self.session.begin():
                    import_files(self.session, macro_path=self.macros, transactions_path=invalid)
        self.assertEqual(aggregate_transactions(self.session).transaction_count, 0)

    def test_rejects_duplicate_weeks_and_invalid_csv_structure(self):
        duplicate = self.write_csv("duplicate.csv", [MACRO, MACRO])
        with self.assertRaisesRegex(CSVImportError, "повторяется"), self.session.begin():
            import_files(self.session, macro_path=duplicate, batch_size=1)
        for text in [
            '"unclosed header\n',
            "week,week\n2019-01-07,2019-01-07\n",
            ",".join(MACRO) + "\n2019-01-07,1\n",
            ",".join(MACRO) + '\n"unterminated',
        ]:
            with self.subTest(text=text):
                path = self.directory / "malformed.csv"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(CSVImportError), self.session.begin():
                    import_files(self.session, macro_path=path)

    def test_broken_header_reports_file_and_line(self):
        path = self.directory / "header.csv"
        path.write_text('"unclosed header\n', encoding="utf-8")
        with self.assertRaisesRegex(CSVImportError, "header.csv, строка 1"):
            with self.session.begin():
                import_files(self.session, macro_path=path)
