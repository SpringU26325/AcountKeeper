"""SQLite persistence for AccountKeeper."""

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Iterator

from config import CSV_FIELDS, CSV_PATH, DB_PATH


@dataclass
class Account:
    """A single expense record."""

    record_id: int
    record_date: str
    amount: Decimal
    category: str
    note: str


class AccountStore:
    """Persist account records in a local SQLite database."""

    def __init__(self, path: Path = DB_PATH, legacy_csv_path: Path = CSV_PATH) -> None:
        self.path = path
        self.legacy_csv_path = legacy_csv_path
        self.records: list[Account] = []
        self._initialize_database()
        self._migrate_legacy_csv()
        self.load()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_date TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT NOT NULL
                )
                """
            )

    def _migrate_legacy_csv(self) -> None:
        """Migrate the previous CSV once when the new database is empty."""
        if not self.legacy_csv_path.exists():
            return
        with self._connect() as connection:
            has_records = connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
            if has_records:
                return
            try:
                with self.legacy_csv_path.open("r", newline="", encoding="utf-8") as file:
                    reader = csv.DictReader(file)
                    if reader.fieldnames != list(CSV_FIELDS):
                        return
                    migrated = []
                    for row in reader:
                        record_id = int(row["id"])
                        record_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                        amount = Decimal(row["amount"])
                        if record_id < 1 or amount == 0 or not row["category"].strip():
                            return
                        migrated.append(
                            (
                                record_id,
                                record_date.isoformat(),
                                f"{amount:.2f}",
                                row["category"].strip(),
                                row["note"].strip(),
                            )
                        )
            except (OSError, csv.Error, ValueError, InvalidOperation, TypeError):
                return
            connection.executemany(
                "INSERT INTO accounts (id, record_date, amount, category, note) "
                "VALUES (?, ?, ?, ?, ?)",
                migrated,
            )

    def load(self) -> None:
        """Read all records from SQLite."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts ORDER BY id"
            ).fetchall()
        self.records = [
            Account(record_id, record_date, Decimal(amount), category, note)
            for record_id, record_date, amount, category, note in rows
        ]

    def next_id(self) -> int:
        return max((record.record_id for record in self.records), default=0) + 1

    def add(
        self, record_date: str, amount: Decimal, category: str, note: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO accounts (record_date, amount, category, note) "
                "VALUES (?, ?, ?, ?)",
                (record_date, f"{amount:.2f}", category.strip(), note.strip()),
            )
        self.load()

    def delete(self, record_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (record_id,))
            deleted = cursor.rowcount > 0
        if not deleted:
            return False
        self.load()
        return True

    def update(
        self,
        record_id: int,
        record_date: str,
        amount: Decimal,
        category: str,
        note: str,
    ) -> bool:
        """更新指定记录，记录不存在或数据库操作失败时返回 False。"""
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE accounts SET record_date = ?, amount = ?, "
                    "category = ?, note = ? WHERE id = ?",
                    (
                        record_date,
                        f"{amount:.2f}",
                        category.strip(),
                        note.strip(),
                        record_id,
                    ),
                )
                updated = cursor.rowcount > 0
        except sqlite3.Error:
            return False
        if not updated:
            return False
        self.load()
        return True

    def export_month_csv(self, month: str) -> Path | None:
        """Export one month's records and return the file path when successful."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts WHERE record_date LIKE ? ORDER BY id",
                (f"{month}-%",),
            ).fetchall()
        if not rows:
            return None

        path = self.path.parent / f"account_export_{month}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_FIELDS)
            writer.writerows(rows)
        return path
