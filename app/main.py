import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from .window import FinanceWindow
from .store import AppStorage


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data.json"
DB_FILE = BASE_DIR / "finance.db"


def main() -> None:
	app = QApplication(sys.argv)
	app.setApplicationName("Финансовый калькулятор")

	storage = AppStorage(DB_FILE, legacy_json_path=DATA_FILE)
	window = FinanceWindow(storage)
	window.show()

	sys.exit(app.exec())


if __name__ == "__main__":
	main()
