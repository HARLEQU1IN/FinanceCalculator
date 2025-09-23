from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
import json
from .finance import FinanceState, Transaction, CATEGORY_ORDER, Goal


class SQLiteStorage:
	def __init__(self, path: Path) -> None:
		self.path = Path(path)
		self.conn = sqlite3.connect(str(self.path))
		self.conn.row_factory = sqlite3.Row
		self._init_schema()

	def _init_schema(self) -> None:
		cur = self.conn.cursor()
		cur.execute(
			"""
			CREATE TABLE IF NOT EXISTS transactions (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				date TEXT NOT NULL,
				type TEXT NOT NULL CHECK(type IN ('income','expense')),
				category TEXT,
				amount REAL NOT NULL,
				note TEXT,
				tags TEXT
			);
			"""
		)
		cur.execute(
			"""
			CREATE TABLE IF NOT EXISTS balances (
				category TEXT PRIMARY KEY,
				amount REAL NOT NULL
			);
			"""
		)
		cur.execute(
			"""
			CREATE TABLE IF NOT EXISTS settings (
				key TEXT PRIMARY KEY,
				value TEXT NOT NULL
			);
			"""
		)
		cur.execute(
			"""
			CREATE TABLE IF NOT EXISTS goals (
				name TEXT PRIMARY KEY,
				target_amount REAL NOT NULL,
				target_date TEXT NOT NULL
			);
			"""
		)
		cur.execute(
			"""
			CREATE TABLE IF NOT EXISTS budgets (
				category TEXT PRIMARY KEY,
				limit_amount REAL NOT NULL
			);
			"""
		)
		# Indices for performance
		cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date)")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_type ON transactions(type)")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category)")
		self.conn.commit()
		# Ensure balances rows exist
		for c in CATEGORY_ORDER:
			cur.execute("INSERT OR IGNORE INTO balances(category, amount) VALUES (?, ?)", (c, 0.0))
		self.conn.commit()

	def load_state(self) -> FinanceState:
		st = FinanceState()
		cur = self.conn.cursor()
		# balances
		for row in cur.execute("SELECT category, amount FROM balances"):
			st.balances[row["category"]] = float(row["amount"])
		# history
		rows = cur.execute("SELECT date, type, category, amount, note, tags FROM transactions ORDER BY id ASC").fetchall()
		st.history = [
			Transaction(
				date=r["date"],
				type=r["type"],
				category=r["category"],
				amount=float(r["amount"]),
				note=r["note"] or "",
				tags=[s.strip() for s in (r["tags"] or "").split(",") if s.strip()],
			)
			for r in rows
		]
		# settings
		rows = cur.execute("SELECT key, value FROM settings").fetchall()
		for r in rows:
			try:
				st.settings[r["key"]] = json.loads(r["value"]) if r["value"].startswith("{") or r["value"].startswith("[") else str(r["value"])  # allow simple strings
			except Exception:
				st.settings[r["key"]] = str(r["value"])
		# goals
		rows = cur.execute("SELECT name, target_amount, target_date FROM goals").fetchall()
		for r in rows:
			st.goals[r["name"]] = Goal(target_amount=float(r["target_amount"]), target_date=r["target_date"])
		# budgets
		rows = cur.execute("SELECT category, limit_amount FROM budgets").fetchall()
		for r in rows:
			st.budgets_monthly[r["category"]] = float(r["limit_amount"])
		return st

	def save_state(self, st: FinanceState) -> None:
		cur = self.conn.cursor()
		# balances
		for c, amt in st.balances.items():
			cur.execute("UPDATE balances SET amount=? WHERE category=?", (float(amt), c))
		# clear and re-insert transactions (simple approach)
		cur.execute("DELETE FROM transactions")
		for t in st.history:
			cur.execute(
				"INSERT INTO transactions(date, type, category, amount, note, tags) VALUES (?, ?, ?, ?, ?, ?)",
				(t.date, t.type, t.category, float(t.amount), t.note, ", ".join(t.tags or [])),
			)
		# settings
		for k, v in st.settings.items():
			val = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
			cur.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, val))
		# goals
		for name, g in st.goals.items():
			cur.execute(
				"INSERT INTO goals(name, target_amount, target_date) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET target_amount=excluded.target_amount, target_date=excluded.target_date",
				(name, float(g.target_amount), g.target_date or ""),
			)
		# budgets
		for c, limit_amt in st.budgets_monthly.items():
			cur.execute(
				"INSERT INTO budgets(category, limit_amount) VALUES(?, ?) ON CONFLICT(category) DO UPDATE SET limit_amount=excluded.limit_amount",
				(c, float(limit_amt)),
			)
		self.conn.commit()

	def close(self) -> None:
		try:
			self.conn.close()
		except Exception:
			pass
