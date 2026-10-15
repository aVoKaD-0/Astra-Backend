import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core import MacroRecord, TransactionInput
from core.data import BuildingMaterialTransaction, ImportedTransaction, MacroDriverWeekly
from core.data.queries import MissingMacroWeekError

MACRO_COLUMNS = {
    "week",
    "housing_starts_index",
    "lumber_price_index",
    "mortgage_rate",
    "season_factor",
}
MACRO_FIELDS = ("housing_starts_index", "lumber_price_index", "mortgage_rate")
TRANSACTION_COLUMNS = {
    "date",
    "year",
    "month",
    "week_of_year",
    "region",
    "product_category",
    "sku",
    "channel",
    "customer_type",
    "units",
    "unit_price",
    "revenue",
    *MACRO_FIELDS,
}


class CSVImportError(ValueError):
    pass


@dataclass
class ImportStats:
    macro_rows: int = 0
    transaction_rows: int = 0
    inserted_transactions: int = 0
    skipped_transactions: int = 0


def _read_rows(path: Path, expected: set[str]):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            headers = reader.fieldnames or []
            columns = set(headers)
            duplicates = ", ".join(
                sorted(name for name, count in Counter(headers).items() if count > 1)
            )
            if duplicates or columns != expected:
                missing = ", ".join(sorted(expected - columns)) or "нет"
                extra = ", ".join(sorted(columns - expected)) or "нет"
                raise CSVImportError(
                    f"{path.name}: неверные заголовки; отсутствуют: {missing}; "
                    f"лишние: {extra}; повторяются: {duplicates or 'нет'}"
                )
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise CSVImportError(
                        f"{path.name}, строка {reader.line_num}: неверное число столбцов"
                    )
                yield reader.line_num, row
        except csv.Error as error:
            line = max(reader.line_num, reader.reader.line_num, 1)
            raise CSVImportError(f"{path.name}, строка {line}: {error}") from error


def _row_error(path: Path, line: int, error: Exception) -> CSVImportError:
    if isinstance(error, ValidationError):
        details = error.errors(include_url=False)[0]
        message = f"{'.'.join(map(str, details['loc']))}: {details['msg']}"
    else:
        message = str(error)
    return CSVImportError(f"{path.name}, строка {line}: {message}")


def _save_macros(session: Session, rows: list[dict]) -> None:
    statement = insert(MacroDriverWeekly).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[MacroDriverWeekly.week],
            set_={name: statement.excluded[name] for name in MACRO_COLUMNS if name != "week"},
        )
    )


def _import_macros(session: Session, path: Path, batch_size: int) -> int:
    seen = set()
    batch = []
    count = 0
    for line, row in _read_rows(path, MACRO_COLUMNS):
        try:
            record = MacroRecord.model_validate(row)
            if record.week in seen:
                raise ValueError(f"Неделя {record.week} повторяется в одном файле")
            seen.add(record.week)
        except ValueError as error:
            raise _row_error(path, line, error) from error
        batch.append(record.model_dump())
        count += 1
        if len(batch) >= batch_size:
            _save_macros(session, batch)
            batch = []
    if batch:
        _save_macros(session, batch)
    session.expire_all()
    return count


def _save_transactions(session: Session, batch: list[tuple]) -> int:
    keys = [(fingerprint, occurrence) for fingerprint, occurrence, _ in batch]
    statement = select(ImportedTransaction.fingerprint, ImportedTransaction.occurrence).where(
        tuple_(ImportedTransaction.fingerprint, ImportedTransaction.occurrence).in_(keys)
    )
    existing = set(session.execute(statement))
    pending = [
        (fingerprint, occurrence, row)
        for fingerprint, occurrence, row in batch
        if (fingerprint, occurrence) not in existing
    ]
    if not pending:
        return 0

    statement = insert(BuildingMaterialTransaction).returning(
        BuildingMaterialTransaction.id, sort_by_parameter_order=True
    )
    rows = [row for _, _, row in pending]
    identifiers = list(session.scalars(statement, rows))
    imported_rows = [
        {"fingerprint": fingerprint, "occurrence": occurrence, "transaction_id": identifier}
        for (fingerprint, occurrence, _), identifier in zip(pending, identifiers)
    ]
    session.execute(insert(ImportedTransaction), imported_rows)
    return len(pending)


def _transaction_values(row: dict, macros: dict) -> dict:
    inputs = {name: row[name] for name in TransactionInput.model_fields}
    transaction = TransactionInput.model_validate(inputs)
    values = transaction.to_row()

    for name in ("year", "month", "week_of_year"):
        if int(row[name]) != values[name]:
            raise ValueError(f"{name} не соответствует дате")
    if Decimal(row["revenue"]) != values["revenue"]:
        raise ValueError("revenue не соответствует units × unit_price")

    macro = macros.get(transaction.date)
    if macro is None:
        raise MissingMacroWeekError(transaction.date)
    for name in MACRO_FIELDS:
        number = Decimal(row[name])
        expected = Decimal(str(getattr(macro, name)))
        if not number.is_finite() or number != expected:
            raise ValueError(f"{name} не совпадает с недельными показателями")
    return values


def _import_transactions(session: Session, path: Path, batch_size: int, stats: ImportStats) -> None:
    macros = {record.week: record for record in session.scalars(select(MacroDriverWeekly))}
    occurrences = Counter()
    batch = []
    for line, row in _read_rows(path, TRANSACTION_COLUMNS):
        try:
            values = _transaction_values(row, macros)
        except (ValueError, InvalidOperation) as error:
            raise _row_error(path, line, error) from error

        canonical = json.dumps(values, sort_keys=True, default=str, ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        occurrences[fingerprint] += 1
        batch.append((fingerprint, occurrences[fingerprint], values))
        stats.transaction_rows += 1
        if len(batch) >= batch_size:
            stats.inserted_transactions += _save_transactions(session, batch)
            batch = []
    if batch:
        stats.inserted_transactions += _save_transactions(session, batch)
    stats.skipped_transactions = stats.transaction_rows - stats.inserted_transactions


def import_files(
    session: Session,
    *,
    macro_path: Optional[Path] = None,
    transactions_path: Optional[Path] = None,
    batch_size: int = 1000,
) -> ImportStats:
    if not macro_path and not transactions_path:
        raise ValueError("Укажите хотя бы один CSV-файл")
    if not 1 <= batch_size <= 1000:
        raise ValueError("Размер пакета должен быть от 1 до 1000")
    session.execute(select(func.pg_advisory_xact_lock(2026100701)))
    stats = ImportStats()
    if macro_path:
        stats.macro_rows = _import_macros(session, Path(macro_path), batch_size)
    if transactions_path:
        _import_transactions(session, Path(transactions_path), batch_size, stats)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка CSV в PostgreSQL")
    parser.add_argument("--macro", type=Path, help="CSV недельных показателей")
    parser.add_argument("--transactions", type=Path, help="CSV продаж")
    parser.add_argument("--batch-size", type=int, default=1000)
    arguments = parser.parse_args()
    if not arguments.macro and not arguments.transactions:
        parser.error("Укажите --macro и/или --transactions")

    from core.data.database import SessionLocal

    try:
        with SessionLocal.begin() as session:
            stats = import_files(
                session,
                macro_path=arguments.macro,
                transactions_path=arguments.transactions,
                batch_size=arguments.batch_size,
            )
    except (ValueError, OSError, UnicodeError) as error:
        print(f"Импорт отменён: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Импорт отменён: ошибка БД. Проверьте подключение и схему.", file=sys.stderr)
        return 1
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
