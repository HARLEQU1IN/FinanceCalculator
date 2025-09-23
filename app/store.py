from __future__ import annotations
from pathlib import Path
from typing import Optional
from .finance import FinanceState
from .sqlite_storage import SQLiteStorage
from .storage import Storage as JsonStorage


class AppStorage:
	def __init__(self, db_path: Path, legacy_json_path: Optional[Path] = None) -> None:
		self.db_path = Path(db_path)
		self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
		self.sqlite = SQLiteStorage(self.db_path)
		# Migrate from JSON if DB empty and legacy exists
		state = self.sqlite.load_state()
		if not state.history and self.legacy_json_path and self.legacy_json_path.exists():
			try:
				legacy = JsonStorage(self.legacy_json_path)
				state = legacy.state
				self.sqlite.save_state(state)
			except Exception:
				pass
		self.state: FinanceState = self.sqlite.load_state()

	def save(self) -> None:
		self.sqlite.save_state(self.state)

	def reload(self) -> None:
		self.state = self.sqlite.load_state()

	def reset(self) -> None:
		# Reset to empty state and save
		self.state = FinanceState()
		self.sqlite.save_state(self.state)

	def close(self) -> None:
		self.sqlite.close()
