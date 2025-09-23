from __future__ import annotations
import json
from pathlib import Path
from .finance import FinanceState


class Storage:
	def __init__(self, path: Path) -> None:
		self.path = Path(path)
		self.state = FinanceState()
		self.load()

	def load(self) -> None:
		if self.path.exists():
			try:
				data = json.loads(self.path.read_text(encoding="utf-8"))
				self.state = FinanceState.from_dict(data)
			except Exception:
				self.state = FinanceState()

	def save(self) -> None:
		data = self.state.to_dict()
		self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

	def reset(self) -> None:
		self.state = FinanceState()
		self.save()

