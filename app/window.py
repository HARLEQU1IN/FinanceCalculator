from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QDate, QTimer
from PySide6.QtGui import QAction, QPalette, QColor
from PySide6.QtWidgets import (
	QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
	QTabWidget, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QFormLayout,
	QSpinBox, QDoubleSpinBox, QFileDialog, QMenu, QStyledItemDelegate, QDialog, QDialogButtonBox, QDateTimeEdit, QDateEdit, QProgressBar, QTextBrowser
)

from .finance import (
	FinanceState,
	CATEGORY_ORDER,
	CATEGORY_LABELS_RU,
	add_income,
	add_expense,
	percent_breakdown,
	split_income,
	future_value_simple,
	future_value_compound,
	projection_monthly_contrib,
	goal_progress,
	monthly_category_spent,
	month_key,
	filter_transactions,
)
from .store import AppStorage as Storage

# Matplotlib embed
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import pandas as pd
import requests

SUPPORTED_CURRENCIES = [
	"RUB", "USD", "EUR", "GBP", "CNY", "JPY", "KZT", "UAH"
]


class CategoryDelegate(QStyledItemDelegate):
	def createEditor(self, parent, option, index):
		combo = QComboBox(parent)
		for c in CATEGORY_ORDER:
			combo.addItem(CATEGORY_LABELS_RU[c], c)
		return combo

	def setEditorData(self, editor, index):
		label = index.data(Qt.EditRole) or index.data()
		for i in range(editor.count()):
			if editor.itemText(i) == label:
				editor.setCurrentIndex(i)
				return
		editor.setCurrentIndex(0)

	def setModelData(self, editor, model, index):
		model.setData(index, editor.currentText(), Qt.EditRole)


class EditTransactionDialog(QDialog):
	def __init__(self, parent, transaction):
		super().__init__(parent)
		self.setWindowTitle("Редактировать транзакцию")
		self.transaction = transaction

		form = QFormLayout(self)

		self.dt = QDateTimeEdit(self)
		self.dt.setDisplayFormat("yyyy-MM-dd HH:mm")
		self.dt.setCalendarPopup(True)
		from datetime import datetime
		try:
			d = datetime.strptime(transaction.date, "%Y-%m-%d %H:%M")
			from PySide6.QtCore import QDateTime
			self.dt.setDateTime(QDateTime(d.year, d.month, d.day, d.hour, d.minute))
		except Exception:
			pass

		self.type_label = QLabel("Доход" if transaction.type == "income" else "Расход")

		self.cat = QComboBox(self)
		for c in CATEGORY_ORDER:
			self.cat.addItem(CATEGORY_LABELS_RU[c], c)
		if transaction.category:
			for i in range(self.cat.count()):
				if self.cat.itemData(i) == transaction.category:
					self.cat.setCurrentIndex(i)
					break
		self.cat.setEnabled(transaction.type == "expense")

		self.amount = QDoubleSpinBox(self)
		self.amount.setMaximum(1_000_000_000)
		self.amount.setDecimals(2)
		self.amount.setValue(float(transaction.amount))
		self.amount.setSuffix(" ₽")
		self.amount.setEnabled(transaction.type == "expense")

		self.note = QLineEdit(self)
		self.note.setText(transaction.note or "")

		self.tags = QLineEdit(self)
		self.tags.setPlaceholderText("теги через запятую")
		self.tags.setText(", ".join(transaction.tags or []))

		form.addRow("Дата и время:", self.dt)
		form.addRow("Тип:", self.type_label)
		form.addRow("Категория:", self.cat)
		form.addRow("Сумма:", self.amount)
		form.addRow("Примечание:", self.note)
		form.addRow("Теги:", self.tags)

		buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)
		form.addRow(buttons)

	def get_values(self):
		qdt = self.dt.dateTime()
		date_str = f"{qdt.date().year()}-{qdt.date().month():02d}-{qdt.date().day():02d} {qdt.time().hour():02d}:{qdt.time().minute():02d}"
		cat = self.cat.currentData()
		amount = float(self.amount.value())
		note = self.note.text().strip()
		tags = [s.strip() for s in self.tags.text().split(",") if s.strip()]
		return date_str, cat, amount, note, tags


class FinanceWindow(QMainWindow):
	def __init__(self, storage: Storage) -> None:
		super().__init__()
		self.storage = storage
		self.setWindowTitle("Финансовый калькулятор")
		self.resize(1250, 860)

		self._in_table_update = False
		self._backup_timer = QTimer(self)
		self._backup_timer.setTimerType(Qt.VeryCoarseTimer)
		self._backup_timer.timeout.connect(self._auto_backup_tick)

		self.tabs = QTabWidget()
		self.setCentralWidget(self.tabs)

		self._build_dashboard_tab()
		self._build_transactions_tab()
		self._build_interest_tab()
		self._build_settings_tab()
		self._build_charts_tab()
		self._build_goals_budgets_tab()
		self._build_help_tab()
		self._build_export_tab()
		self._build_currency_tab()

		self._build_menu()
		self._apply_theme(self.storage.state.settings.get("theme", "light"))
		self._refresh_all()

	def closeEvent(self, event):
		self.storage.save()
		event.accept()

	def _build_menu(self) -> None:
		menu = self.menuBar().addMenu("Файл")
		a_save = QAction("Сохранить", self)
		a_save.triggered.connect(self.storage.save)
		menu.addAction(a_save)

		a_export = QAction("Экспорт истории в CSV", self)
		a_export.triggered.connect(self._export_csv)
		menu.addAction(a_export)

		a_export_xlsx = QAction("Экспорт в Excel (.xlsx)", self)
		a_export_xlsx.triggered.connect(self._export_excel)
		menu.addAction(a_export_xlsx)

		a_reset = QAction("Сбросить данные", self)
		a_reset.triggered.connect(self._reset_data)
		menu.addAction(a_reset)

	def _build_dashboard_tab(self) -> None:
		w = QWidget()
		layout = QVBoxLayout(w)

		self.income_input = QDoubleSpinBox()
		self.income_input.setPrefix("Доход: ")
		self.income_input.setSuffix(" ₽")
		self.income_input.setMaximum(1_000_000_000)
		self.income_input.setDecimals(2)

		btn_calc = QPushButton("Рассчитать распределение и добавить доход")
		btn_calc.clicked.connect(self._handle_add_income)

		layout.addWidget(self.income_input)
		layout.addWidget(btn_calc)

		self.breakdown_label = QLabel("")
		self.breakdown_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
		layout.addWidget(self.breakdown_label)

		self.balances_label = QLabel("")
		self.balances_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
		layout.addWidget(self.balances_label)

		self.tabs.addTab(w, "Дашборд")

	def _build_transactions_tab(self) -> None:
		w = QWidget()
		layout = QVBoxLayout(w)

		filters = QHBoxLayout()
		self.filter_text = QLineEdit()
		self.filter_text.setPlaceholderText("поиск по примечанию/категории")
		self.filter_tag = QLineEdit()
		self.filter_tag.setPlaceholderText("тег")
		self.filter_month = QComboBox()
		self.filter_month.addItem("Все месяцы", "")
		self.filter_text.textChanged.connect(self._refresh_table)
		self.filter_tag.textChanged.connect(self._refresh_table)
		self.filter_month.currentIndexChanged.connect(self._refresh_table)
		filters.addWidget(QLabel("Фильтр:"))
		filters.addWidget(self.filter_text)
		filters.addWidget(self.filter_tag)
		filters.addWidget(self.filter_month)
		layout.addLayout(filters)

		row = QHBoxLayout()
		self.expense_category = QComboBox()
		for c in CATEGORY_ORDER:
			self.expense_category.addItem(CATEGORY_LABELS_RU[c], c)
		self.expense_amount = QDoubleSpinBox()
		self.expense_amount.setMaximum(1_000_000_000)
		self.expense_amount.setDecimals(2)
		self.expense_amount.setPrefix("Расход: ")
		self.expense_amount.setSuffix(" ₽")
		self.expense_note = QLineEdit()
		self.expense_note.setPlaceholderText("примечание")
		self.expense_tags = QLineEdit()
		self.expense_tags.setPlaceholderText("теги через запятую")
		btn_add_expense = QPushButton("Добавить расход")
		btn_add_expense.clicked.connect(self._handle_add_expense)

		row.addWidget(self.expense_category)
		row.addWidget(self.expense_amount)
		row.addWidget(self.expense_note)
		row.addWidget(self.expense_tags)
		row.addWidget(btn_add_expense)
		layout.addLayout(row)

		self.table = QTableWidget(0, 6)
		self.table.setHorizontalHeaderLabels(["Дата", "Тип", "Категория", "Сумма", "Примечание", "Теги"])
		self.table.horizontalHeader().setStretchLastSection(True)
		self.table.setItemDelegateForColumn(2, CategoryDelegate(self.table))
		self.table.itemChanged.connect(self._on_table_item_changed)
		self.table.setContextMenuPolicy(Qt.CustomContextMenu)
		self.table.customContextMenuRequested.connect(self._open_table_menu)
		self.table.cellDoubleClicked.connect(self._cell_double_clicked)
		layout.addWidget(self.table)

		self.tabs.addTab(w, "Транзакции")

	def _build_interest_tab(self) -> None:
		w = QWidget()
		form = QFormLayout(w)

		self.dep_balance = QDoubleSpinBox()
		self.dep_balance.setMaximum(1_000_000_000)
		self.dep_rate = QDoubleSpinBox()
		self.dep_rate.setSuffix(" % годовых")
		self.dep_rate.setMaximum(100.0)
		self.dep_years = QDoubleSpinBox()
		self.dep_years.setSuffix(" лет")
		self.dep_years.setMaximum(100.0)

		self.dep_compound = QComboBox()
		self.dep_compound.addItems(["Простые проценты", "Сложные проценты (ежемес.)"])

		btn_calc = QPushButton("Рассчитать будущий баланс")
		btn_calc.clicked.connect(self._handle_calc_interest)

		self.dep_result = QLabel("")

		form.addRow("Сумма на счёте:", self.dep_balance)
		form.addRow("Ставка:", self.dep_rate)
		form.addRow("Срок:", self.dep_years)
		form.addRow("Тип процентов:", self.dep_compound)
		form.addRow(btn_calc)
		form.addRow(self.dep_result)

		self.tabs.addTab(w, "Вклад и прогнозы")

	def _build_settings_tab(self) -> None:
		w = QWidget()
		root = QVBoxLayout(w)

		hdr1 = QLabel("Основные настройки")
		hdr1.setStyleSheet("font-weight:600; margin: 6px 0;")
		root.addWidget(hdr1)

		form1 = QFormLayout()

		self.base_expenses = QDoubleSpinBox()
		self.base_expenses.setMaximum(1_000_000_000)
		self.base_expenses.setPrefix("Базовые расходы/мес: ")
		self.base_expenses.setSuffix(" ₽")
		self.base_expenses.setToolTip("Используется как ориентир для 50% обязательных расходов")
		self.base_expenses.valueChanged.connect(self._save_base_expenses)
		form1.addRow("Базовые расходы (для 50%):", self.base_expenses)

		self.theme_combo = QComboBox()
		self.theme_combo.addItems(["Светлая", "Тёмная"])
		self.theme_combo.setToolTip("Переключение моментально применяет тему ко всем вкладкам")
		self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
		form1.addRow("Тема интерфейса:", self.theme_combo)

		self.lang_combo = QComboBox()
		self.lang_combo.addItems(["Русский", "English"])
		self.lang_combo.setToolTip("Для применения языка требуется перезапуск приложения")
		self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
		form1.addRow("Язык (перезапуск):", self.lang_combo)

		desc1 = QLabel("Подсказка: тёмная тема удобна вечером. Язык влияет на подписи и тексты.")
		desc1.setWordWrap(True)
		root.addLayout(form1)
		root.addWidget(desc1)

		hdr3 = QLabel("Автоматическое резервное копирование")
		hdr3.setStyleSheet("font-weight:600; margin: 12px 0 6px;")
		root.addWidget(hdr3)

		form_backup = QFormLayout()
		self.backup_enable = QComboBox()
		self.backup_enable.addItems(["Выкл.", "Вкл."])
		self.backup_enable.currentIndexChanged.connect(self._on_backup_settings_changed)
		form_backup.addRow("Автобэкап:", self.backup_enable)

		self.backup_minutes = QSpinBox()
		self.backup_minutes.setRange(5, 1440)
		self.backup_minutes.setSingleStep(5)
		self.backup_minutes.setSuffix(" мин")
		self.backup_minutes.setToolTip("Интервал между автоматическими бэкапами")
		self.backup_minutes.valueChanged.connect(self._on_backup_settings_changed)
		form_backup.addRow("Интервал:", self.backup_minutes)
		root.addLayout(form_backup)

		hdr2 = QLabel("Данные и сброс")
		hdr2.setStyleSheet("font-weight:600; margin: 12px 0 6px;")
		root.addWidget(hdr2)

		row_actions = QHBoxLayout()
		btn_reset_settings = QPushButton("Сбросить настройки")
		btn_reset_settings.setToolTip("Вернуть тему, язык и автобэкап по умолчанию")
		btn_reset_settings.clicked.connect(self._reset_settings)
		row_actions.addWidget(btn_reset_settings)
		row_actions.addStretch(1)
		root.addLayout(row_actions)

		self.tabs.addTab(w, "Настройки")

	def _build_charts_tab(self) -> None:
		w = QWidget()
		layout = QVBoxLayout(w)
		controls = QHBoxLayout()
		controls.addWidget(QLabel("Месяц для круговой диаграммы:"))
		self.charts_month = QComboBox()
		self.charts_month.addItem("Текущий", "")
		self.charts_month.currentIndexChanged.connect(self._refresh_charts)
		controls.addWidget(self.charts_month)
		controls.addStretch(1)
		layout.addLayout(controls)

		self.fig = Figure(figsize=(8, 6))
		self.canvas = FigureCanvas(self.fig)
		layout.addWidget(self.canvas)
		self.tabs.addTab(w, "Графики")

	def _build_goals_budgets_tab(self) -> None:
		w = QWidget()
		form = QFormLayout(w)

		# Goals widgets
		self.goal_growth_amount = QDoubleSpinBox()
		self.goal_growth_amount.setMaximum(1_000_000_000)
		self.goal_growth_date = QDateEdit()
		self.goal_growth_date.setCalendarPopup(True)
		self.goal_growth_progress = QProgressBar()

		self.goal_stab_amount = QDoubleSpinBox()
		self.goal_stab_amount.setMaximum(1_000_000_000)
		self.goal_stab_date = QDateEdit()
		self.goal_stab_date.setCalendarPopup(True)
		self.goal_stab_progress = QProgressBar()

		self.goal_growth_amount.valueChanged.connect(self._save_goals)
		self.goal_stab_amount.valueChanged.connect(self._save_goals)
		self.goal_growth_date.dateChanged.connect(self._save_goals)
		self.goal_stab_date.dateChanged.connect(self._save_goals)

		form.addRow(QLabel("Цели накоплений:"))
		form.addRow("Рост (growth) — сумма:", self.goal_growth_amount)
		form.addRow("Рост (growth) — дата:", self.goal_growth_date)
		form.addRow("Прогресс по росту:", self.goal_growth_progress)
		form.addRow("Стабильность (stability) — сумма:", self.goal_stab_amount)
		form.addRow("Стабильность (stability) — дата:", self.goal_stab_date)
		form.addRow("Прогресс по стабильности:", self.goal_stab_progress)

		# Budgets widgets
		self.budget_inputs = {}
		form.addRow(QLabel("Месячные лимиты по категориям:"))
		for c in CATEGORY_ORDER:
			spin = QDoubleSpinBox()
			spin.setMaximum(1_000_000_000)
			spin.setSuffix(" ₽")
			spin.valueChanged.connect(self._save_budgets)
			self.budget_inputs[c] = spin
			form.addRow(CATEGORY_LABELS_RU[c] + ":", spin)

		self.budget_status = QLabel("")
		self.budget_status.setWordWrap(True)
		form.addRow(self.budget_status)

		self.tabs.addTab(w, "Цели и бюджет")

	def _update_backup_controls_visibility(self) -> None:
		enabled = self.backup_enable.currentIndex() == 1
		mode_idx = self.backup_mode.currentIndex()
		show_interval = enabled and mode_idx == 0
		self.backup_minutes.setVisible(show_interval)
		self.backup_minutes_label.setVisible(show_interval)
		self.backup_mode.setEnabled(enabled)

	def _on_backup_settings_changed(self) -> None:
		enabled = self.backup_enable.currentIndex() == 1
		minutes = int(self.backup_minutes.value())
		self.storage.state.settings["backup_enabled"] = "1" if enabled else "0"
		self.storage.state.settings["backup_interval_minutes"] = str(minutes)
		self.storage.save()
		self._configure_backup_timer()

	def _configure_backup_timer(self) -> None:
		enabled = str(self.storage.state.settings.get("backup_enabled", "0")) == "1"
		try:
			minutes = int(self.storage.state.settings.get("backup_interval_minutes", "60"))
		except Exception:
			minutes = 60
		if not enabled:
			self._backup_timer.stop()
			return
		self._backup_timer.stop()
		self._backup_timer.start(max(5, minutes) * 60 * 1000)

	def _restore_backup(self) -> None:
		path, _ = QFileDialog.getOpenFileName(self, "Выберите файл бэкапа", str(Path(__file__).resolve().parent.parent / "backups"), "DB (*.db)")
		if not path:
			return
		base = Path(__file__).resolve().parent.parent
		db = base / "finance.db"
		import shutil
		try:
			shutil.copy2(Path(path), db)
			self.storage.reload()
			self._refresh_all()
			QMessageBox.information(self, "Восстановление", "База восстановлена из бэкапа.")
		except Exception as e:
			QMessageBox.warning(self, "Ошибка", f"Не удалось восстановить: {e}")

	def _build_currency_tab(self) -> None:
		w = QWidget()
		v = QVBoxLayout(w)
		row = QHBoxLayout()
		self.cur_amount = QDoubleSpinBox()
		self.cur_amount.setMaximum(1_000_000_000)
		self.cur_amount.setDecimals(4)
		self.cur_amount.setValue(1.0)
		self.cur_from = QComboBox()
		self.cur_from.addItems(SUPPORTED_CURRENCIES)
		swap_btn = QPushButton("⇄")
		swap_btn.setFixedWidth(40)
		swap_btn.clicked.connect(self._swap_currencies)
		self.cur_to = QComboBox()
		self.cur_to.addItems(SUPPORTED_CURRENCIES)
		btn_conv = QPushButton("Конвертировать")
		btn_conv.clicked.connect(self._handle_convert_currency)
		row.addWidget(QLabel("Сумма:"))
		row.addWidget(self.cur_amount)
		row.addWidget(self.cur_from)
		row.addWidget(swap_btn)
		row.addWidget(self.cur_to)
		row.addWidget(btn_conv)
		v.addLayout(row)

		info = QHBoxLayout()
		self.cur_rate = QLabel("Курс: —")
		self.cur_updated = QLabel("Обновлено: —")
		info.addWidget(self.cur_rate)
		info.addWidget(self.cur_updated)
		info.addStretch(1)
		v.addLayout(info)

		self.cur_result = QLabel("—")
		font = self.cur_result.font()
		font.setPointSize(font.pointSize() + 4)
		self.cur_result.setFont(font)
		v.addWidget(self.cur_result)
		v.addStretch(1)
		self.tabs.addTab(w, "Конвертер валют")

	def _swap_currencies(self) -> None:
		fi = self.cur_from.currentIndex()
		ti = self.cur_to.currentIndex()
		self.cur_from.setCurrentIndex(ti)
		self.cur_to.setCurrentIndex(fi)

	def _fetch_rate_multi(self, from_code: str, to_code: str) -> tuple[float, str]:
		# Try several providers with short timeouts
		providers = [
			(lambda f, t: f"https://api.exchangerate.host/convert?from={f}&to={t}", lambda j: (float(j.get("info", {}).get("rate", 0.0)), j.get("date", ""))),
			(lambda f, t: f"https://open.er-api.com/v6/latest/{f}", lambda j: (float(j.get("rates", {}).get(t, 0.0)), j.get("time_last_update_utc", ""))),
			(lambda f, t: f"https://api.frankfurter.app/latest?from={f}&to={t}", lambda j: (float(j.get("rates", {}).get(t, 0.0)), j.get("date", ""))),
		]
		for make_url, parse in providers:
			try:
				url = make_url(from_code, to_code)
				resp = requests.get(url, timeout=6)
				if resp.status_code != 200:
					continue
				j = resp.json()
				rate, date_str = parse(j)
				if rate and rate > 0:
					return rate, date_str or ""
			except Exception:
				continue
		return 0.0, ""

	def _handle_convert_currency(self) -> None:
		from_code = self.cur_from.currentText()
		to_code = self.cur_to.currentText()
		amount = float(self.cur_amount.value())
		if from_code == to_code:
			self.cur_rate.setText("Курс: 1.0")
			self.cur_updated.setText("Обновлено: сейчас")
			self.cur_result.setText(f"{amount:.4f} {to_code}")
			return
		rate, date_str = self._fetch_rate_multi(from_code, to_code)
		if rate <= 0:
			QMessageBox.warning(self, "Ошибка", "Не удалось получить курс с нескольких источников. Проверьте интернет/файрвол.")
			return
		res = amount * rate
		self.cur_rate.setText(f"Курс: 1 {from_code} = {rate:.6f} {to_code}")
		self.cur_updated.setText(f"Обновлено: {date_str or '—'}")
		self.cur_result.setText(f"{res:,.4f} {to_code}".replace(",", " "))

	def _apply_theme(self, theme: str) -> None:
		# Apply simple stylesheet (previous working behavior)
		if theme == "dark" or theme == "Тёмная":
			self.setStyleSheet(
				"""
				QWidget { background: #121212; color: #e0e0e0; }
				QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTableWidget, QTextBrowser { background: #1e1e1e; color: #e0e0e0; border: 1px solid #333; }
				QPushButton { background: #2b2b2b; border: 1px solid #444; padding: 6px 10px; }
				QPushButton:hover { background: #333333; }
				QHeaderView::section { background: #1e1e1e; color: #e0e0e0; border: 1px solid #333; }
				QTabBar::tab { background: #1e1e1e; color: #e0e0e0; padding: 6px 10px; border: 1px solid #333; border-bottom: none; }
				QTabBar::tab:selected { background: #2b2b2b; }
				QTabWidget::pane { border: 1px solid #333; }
				QMenuBar { background: #1e1e1e; color: #e0e0e0; }
				QMenu { background: #1e1e1e; color: #e0e0e0; border: 1px solid #333; }
				QMenu::item:selected { background: #2b2b2b; }
				QToolTip { color: #e0e0e0; background-color: #2b2b2b; border: 1px solid #444; }
				"""
			)
			if hasattr(self, "theme_combo"):
				self.theme_combo.setCurrentIndex(1)
		else:
			self.setStyleSheet("")
			if hasattr(self, "theme_combo"):
				self.theme_combo.setCurrentIndex(0)

	def _on_theme_changed(self) -> None:
		idx = self.theme_combo.currentIndex() if hasattr(self, 'theme_combo') else 0
		theme = "dark" if idx == 1 else "light"
		self.storage.state.settings["theme"] = theme
		self.storage.save()
		self._apply_theme(theme)

	def _on_lang_changed(self) -> None:
		idx = self.lang_combo.currentIndex()
		lang = "ru" if idx == 0 else "en"
		self.storage.state.settings["language"] = lang
		self.storage.save()
		QMessageBox.information(self, "Язык", "Изменение языка вступит в силу после перезапуска приложения.")

	def _refresh_charts(self) -> None:
		st = self.storage.state
		# Populate month selector
		months = sorted({month_key(t.date) for t in st.history if t.date})
		self.charts_month.blockSignals(True)
		cur = self.charts_month.currentData()
		self.charts_month.clear()
		self.charts_month.addItem("Текущий", "")
		for m in months:
			self.charts_month.addItem(m, m)
		if cur:
			idx = self.charts_month.findData(cur)
			if idx >= 0:
				self.charts_month.setCurrentIndex(idx)
		self.charts_month.blockSignals(False)

		self.fig.clear()
		if not st.history:
			self.canvas.draw()
			return

		# DataFrame with signed amounts
		rows = []
		for t in st.history:
			rows.append({
				"date": t.date[:7],
				"type": t.type,
				"category": t.category or "",
				"amount": t.amount if t.type == "income" else -abs(t.amount),
			})
		df = pd.DataFrame(rows)
		df_month = df.groupby(["date", "type"]).sum().reset_index()
		pvt = df_month.pivot(index="date", columns="type", values="amount").fillna(0).sort_index()
		pvt["net"] = pvt.get("income", 0) + pvt.get("expense", 0)

		ax1 = self.fig.add_subplot(221)
		if "income" in pvt:
			ax1.plot(pvt.index, pvt["income"], label="Доходы", color="#2e7d32", marker="o")
		if "expense" in pvt:
			ax1.plot(pvt.index, -pvt["expense"], label="Расходы", color="#c62828", marker="o")
		ax1.set_title("Доходы/Расходы")
		ax1.grid(True, alpha=0.3)
		ax1.legend()

		ax2 = self.fig.add_subplot(222)
		ax2.bar(pvt.index, pvt["net"], color=["#2e7d32" if v >= 0 else "#c62828" for v in pvt["net"]])
		ax2.set_title("Нетто по месяцам")
		ax2.axhline(0, color="#888", linewidth=0.8)

		# Pie: expenses by category for selected or latest month
		sel_month = self.charts_month.currentData() or (pvt.index[-1] if len(pvt.index) else "")
		ax3 = self.fig.add_subplot(223)
		df_exp = df[(df["type"] == "expense") & (df["date"] == sel_month)]
		if not df_exp.empty:
			pie = df_exp.groupby("category")["amount"].sum().abs().sort_values(ascending=False)
			labels = [CATEGORY_LABELS_RU.get(c, c) for c in pie.index]
			ax3.pie(pie.values, labels=labels, autopct="%1.0f%%", startangle=90, textprops={"color": "#e0e0e0" if self.storage.state.settings.get("theme") == "dark" else "#000"})
			ax3.set_title(f"Расходы по категориям ({sel_month})")
		else:
			ax3.text(0.5, 0.5, "Нет данных", ha="center", va="center")
			ax3.set_axis_off()

		# Stacked bar: expenses per category over months
		ax4 = self.fig.add_subplot(224)
		df_exp_all = df[df["type"] == "expense"].copy()
		if not df_exp_all.empty:
			pvt_exp = df_exp_all.pivot_table(index="date", columns="category", values="amount", aggfunc="sum").fillna(0).abs().sort_index()
			bottom = None
			for cat in CATEGORY_ORDER:
				col = pvt_exp.get(cat, None)
				if col is None:
					continue
				vals = col.values
				ax4.bar(pvt_exp.index, vals, bottom=bottom, label=CATEGORY_LABELS_RU[cat])
				bottom = vals if bottom is None else bottom + vals
			ax4.set_title("Структура расходов по месяцам")
			ax4.legend(fontsize=8)
		else:
			ax4.text(0.5, 0.5, "Нет данных", ha="center", va="center")
			ax4.set_axis_off()

		self.fig.tight_layout()
		self.canvas.draw()

	def _refresh_goals_budgets(self) -> None:
		st = self.storage.state
		ga = st.goals.get("growth")
		if ga:
			self.goal_growth_amount.blockSignals(True)
			self.goal_growth_amount.setValue(float(ga.target_amount))
			self.goal_growth_amount.blockSignals(False)
			try:
				y, m, d = [int(x) for x in (ga.target_date or "").split("-")]
				self.goal_growth_date.blockSignals(True)
				self.goal_growth_date.setDate(QDate(y, m, d))
				self.goal_growth_date.blockSignals(False)
			except Exception:
				pass
			pr = goal_progress(st.balances.get("growth", 0.0), ga.target_amount)
			self.goal_growth_progress.setValue(int(pr))
		gs = st.goals.get("stability")
		if gs:
			self.goal_stab_amount.blockSignals(True)
			self.goal_stab_amount.setValue(float(gs.target_amount))
			self.goal_stab_amount.blockSignals(False)
			try:
				y, m, d = [int(x) for x in (gs.target_date or "").split("-")]
				self.goal_stab_date.blockSignals(True)
				self.goal_stab_date.setDate(QDate(y, m, d))
				self.goal_stab_date.blockSignals(False)
			except Exception:
				pass
			pr2 = goal_progress(st.balances.get("stability", 0.0), gs.target_amount)
			self.goal_stab_progress.setValue(int(pr2))

		cur_month = QDate.currentDate().toString("yyyy-MM")
		spent = monthly_category_spent(st, cur_month)
		lines = [f"Статус бюджетов за {cur_month}:"]
		for c in CATEGORY_ORDER:
			limit = float(st.budgets_monthly.get(c, 0.0))
			val = float(spent.get(c, 0.0))
			flag = " (превышение)" if limit > 0 and val > limit else ""
			lines.append(f"- {CATEGORY_LABELS_RU[c]}: потрачено {val:.2f} ₽ / лимит {limit:.2f} ₽{flag}")
		self.budget_status.setText("\n".join(lines))

	def _refresh_filter_months(self) -> None:
		months = sorted({month_key(t.date) for t in self.storage.state.history if t.date})
		cur = self.filter_month.currentData() if hasattr(self, 'filter_month') else ""
		self.filter_month.blockSignals(True)
		self.filter_month.clear()
		self.filter_month.addItem("Все месяцы", "")
		for m in months:
			self.filter_month.addItem(m, m)
		if cur:
			idx = self.filter_month.findData(cur)
			if idx >= 0:
				self.filter_month.setCurrentIndex(idx)
		self.filter_month.blockSignals(False)

	def _refresh_all(self) -> None:
		self.base_expenses.setValue(self.storage.state.base_monthly_expenses)
		theme = self.storage.state.settings.get("theme", "light")
		self._apply_theme(theme)
		if hasattr(self, "theme_combo"):
			self.theme_combo.setCurrentIndex(1 if theme == "dark" else 0)
		lang = self.storage.state.settings.get("language", "ru")
		self.lang_combo.setCurrentIndex(0 if lang == "ru" else 1)
		enabled = str(self.storage.state.settings.get("backup_enabled", "0")) == "1"
		minutes_str = str(self.storage.state.settings.get("backup_interval_minutes", "60"))
		try:
			minutes = int(minutes_str)
		except Exception:
			minutes = 60
		self.backup_enable.setCurrentIndex(1 if enabled else 0)
		self.backup_minutes.blockSignals(True)
		self.backup_minutes.setValue(max(5, min(1440, minutes)))
		self.backup_minutes.blockSignals(False)
		self._configure_backup_timer()
		self._refresh_breakdown_label(0.0)
		self._refresh_balances()
		self._refresh_filter_months()
		self._refresh_table()
		self._refresh_charts()
		self._refresh_goals_budgets()

	def _refresh_breakdown_label(self, amount: float) -> None:
		if amount <= 0:
			self.breakdown_label.setText("Введите доход и нажмите кнопку.")
			return
		bd = percent_breakdown(amount)
		lines = ["Распределение:"]
		for c in CATEGORY_ORDER:
			p = bd[c]["percent"]
			a = bd[c]["amount"]
			lines.append(f"- {CATEGORY_LABELS_RU[c]}: {p}% = {a:,.2f} ₽".replace(",", " "))
		self.breakdown_label.setText("\n".join(lines))

	def _refresh_balances(self) -> None:
		st = self.storage.state
		lines = ["Баланс по категориям:"]
		for c in CATEGORY_ORDER:
			lines.append(f"- {CATEGORY_LABELS_RU[c]}: {st.balances[c]:,.2f} ₽".replace(",", " "))
		self.balances_label.setText("\n".join(lines))

	def _refresh_table(self) -> None:
		st = self.storage.state
		# apply filters
		flt_text = self.filter_text.text().strip()
		flt_tag = self.filter_tag.text().strip()
		flt_month = self.filter_month.currentData()
		items = filter_transactions(st, text=flt_text, tag=flt_tag, month=flt_month)

		self._in_table_update = True
		self.table.blockSignals(True)
		self.table.setRowCount(len(items))
		for i, t in enumerate(items):
			it_date = QTableWidgetItem(t.date)
			it_date.setFlags(it_date.flags() & ~Qt.ItemIsEditable)
			self.table.setItem(i, 0, it_date)

			it_type = QTableWidgetItem("Доход" if t.type == "income" else "Расход")
			it_type.setFlags(it_type.flags() & ~Qt.ItemIsEditable)
			self.table.setItem(i, 1, it_type)

			label = "-" if not t.category else CATEGORY_LABELS_RU[t.category]
			it_cat = QTableWidgetItem(label)
			if t.type == "income":
				it_cat.setFlags(it_cat.flags() & ~Qt.ItemIsEditable)
			self.table.setItem(i, 2, it_cat)

			it_amt = QTableWidgetItem(f"{t.amount:.2f}")
			if t.type == "income":
				it_amt.setFlags(it_amt.flags() & ~Qt.ItemIsEditable)
			self.table.setItem(i, 3, it_amt)

			it_note = QTableWidgetItem(t.note or "")
			self.table.setItem(i, 4, it_note)

			it_tags = QTableWidgetItem(", ".join(t.tags or []))
			self.table.setItem(i, 5, it_tags)
		self.table.blockSignals(False)
		self._in_table_update = False
		self.table.scrollToBottom()

	def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
		if self._in_table_update:
			return
		# edits apply to filtered view; find original
		row = item.row()
		st = self.storage.state
		flt_text = self.filter_text.text().strip()
		flt_tag = self.filter_tag.text().strip()
		flt_month = self.filter_month.currentData()
		items = filter_transactions(st, text=flt_text, tag=flt_tag, month=flt_month)
		if row < 0 or row >= len(items):
			return
		t = items[row]
		col = item.column()

		# Only allow editing: expense category, expense amount, any note, tags
		if col == 4:
			t.note = item.text()
			self.storage.save()
			return
		if col == 5:
			t.tags = [s.strip() for s in (item.text() or "").split(",") if s.strip()]
			self.storage.save()
			return

		if t.type != "expense":
			# revert edit for non-expense fields
			self._refresh_table()
			QMessageBox.information(self, "Изменение отклонено", "Изменять доход можно только в примечании/тегах.")
			return

		old_cat = t.category
		old_amt = t.amount

		new_cat = old_cat
		new_amt = old_amt

		if col == 2:
			label = item.text()
			for key, lbl in CATEGORY_LABELS_RU.items():
				if lbl == label:
					new_cat = key
					break
		elif col == 3:
			try:
				new_amt = float(item.text().replace(",", ".").strip())
			except ValueError:
				QMessageBox.warning(self, "Ошибка", "Введите число для суммы")
				self._refresh_table()
				return

		if new_cat is None:
			new_cat = old_cat

		st.balances[old_cat] = round(st.balances.get(old_cat, 0.0) + old_amt, 2)
		st.balances[new_cat] = round(st.balances.get(new_cat, 0.0) - new_amt, 2)

		t.category = new_cat
		t.amount = round(new_amt, 2)

		self.storage.save()
		self._refresh_balances()
		self._refresh_charts()
		self._refresh_goals_budgets()

	def _open_table_menu(self, pos: QPoint) -> None:
		menu = QMenu(self)
		a_edit = QAction("Редактировать…", self)
		a_edit.triggered.connect(self._edit_selected_transaction)
		menu.addAction(a_edit)
		a_del = QAction("Удалить транзакцию", self)
		a_del.triggered.connect(self._delete_selected_transaction)
		menu.addAction(a_del)
		menu.exec(self.table.viewport().mapToGlobal(pos))

	def _cell_double_clicked(self, row: int, col: int) -> None:
		self._open_edit_dialog(row)

	def _edit_selected_transaction(self) -> None:
		row = self.table.currentRow()
		if row >= 0:
			self._open_edit_dialog(row)

	def _open_edit_dialog(self, row: int) -> None:
		st = self.storage.state
		flt_text = self.filter_text.text().strip()
		flt_tag = self.filter_tag.text().strip()
		flt_month = self.filter_month.currentData()
		items = filter_transactions(st, text=flt_text, tag=flt_tag, month=flt_month)
		if row < 0 or row >= len(items):
			return
		t = items[row]
		dlg = EditTransactionDialog(self, t)
		if dlg.exec() == QDialog.Accepted:
			new_date, new_cat, new_amt, new_note, new_tags = dlg.get_values()
			if t.type == "expense":
				if t.category:
					st.balances[t.category] = round(st.balances.get(t.category, 0.0) + t.amount, 2)
				st.balances[new_cat] = round(st.balances.get(new_cat, 0.0) - new_amt, 2)
			elif t.type == "income":
				alloc_old = split_income(t.amount)
				for cat, val in alloc_old.items():
					st.balances[cat] = round(st.balances.get(cat, 0.0) - val, 2)
				alloc_new = split_income(new_amt)
				for cat, val in alloc_new.items():
					st.balances[cat] = round(st.balances.get(cat, 0.0) + val, 2)
			# update
			t.date = new_date
			if t.type == "expense":
				t.category = new_cat
			t.amount = round(new_amt, 2)
			t.note = new_note
			t.tags = new_tags
			self.storage.save()
			self._refresh_filter_months()
			self._refresh_table()
			self._refresh_balances()
			self._refresh_charts()
			self._refresh_goals_budgets()

	def _delete_selected_transaction(self) -> None:
		row = self.table.currentRow()
		if row < 0:
			return
		st = self.storage.state
		flt_text = self.filter_text.text().strip()
		flt_tag = self.filter_tag.text().strip()
		flt_month = self.filter_month.currentData()
		items = filter_transactions(st, text=flt_text, tag=flt_tag, month=flt_month)
		if row >= len(items):
			return
		t = items[row]
		ans = QMessageBox.question(self, "Удалить", "Удалить выбранную транзакцию и откатить балансы?", QMessageBox.Yes | QMessageBox.No)
		if ans != QMessageBox.Yes:
			return
		if t.type == "expense" and t.category:
			st.balances[t.category] = round(st.balances.get(t.category, 0.0) + t.amount, 2)
		elif t.type == "income":
			alloc = split_income(t.amount)
			for cat, val in alloc.items():
				st.balances[cat] = round(st.balances.get(cat, 0.0) - val, 2)
		# remove from original history
		idx = self.storage.state.history.index(t)
		self.storage.state.history.pop(idx)
		self.storage.save()
		self._refresh_filter_months()
		self._refresh_table()
		self._refresh_balances()
		self._refresh_charts()
		self._refresh_goals_budgets()

	def _handle_add_income(self) -> None:
		amount = float(self.income_input.value())
		if amount <= 0:
			QMessageBox.warning(self, "Внимание", "Введите положительный доход")
			return
		add_income(self.storage.state, amount, note="Доход от пользователя")
		self.storage.save()
		self._refresh_breakdown_label(amount)
		self._refresh_balances()
		self._refresh_filter_months()
		self._refresh_table()
		self._refresh_charts()
		self._refresh_goals_budgets()
		QMessageBox.information(self, "Готово", "Доход добавлен и распределён по 50/25/15/10")

	def _handle_add_expense(self) -> None:
		cat = self.expense_category.currentData()
		amt = float(self.expense_amount.value())
		note = self.expense_note.text().strip()
		tags = [s.strip() for s in self.expense_tags.text().split(",") if s.strip()]
		if amt <= 0:
			QMessageBox.warning(self, "Внимание", "Введите положительный расход")
			return
		add_expense(self.storage.state, cat, amt, note=note, tags=tags)
		self.storage.save()
		self._refresh_balances()
		self._refresh_filter_months()
		self._refresh_table()
		self._refresh_charts()
		self._refresh_goals_budgets()
		self.expense_note.clear()
		self.expense_tags.clear()

	def _handle_calc_interest(self) -> None:
		principal = float(self.dep_balance.value())
		rate = float(self.dep_rate.value())
		years = float(self.dep_years.value())
		compound = self.dep_compound.currentIndex() == 1
		fv = future_value_compound(principal, rate, years) if compound else future_value_simple(principal, rate, years)
		self.dep_result.setText(f"Будущий баланс: {fv:,.2f} ₽".replace(",", " "))

	def _save_base_expenses(self) -> None:
		self.storage.state.base_monthly_expenses = float(self.base_expenses.value())
		self.storage.save()

	def _save_goals(self) -> None:
		st = self.storage.state
		st.goals["growth"].target_amount = float(self.goal_growth_amount.value())
		st.goals["growth"].target_date = self.goal_growth_date.date().toString("yyyy-MM-dd")
		st.goals["stability"].target_amount = float(self.goal_stab_amount.value())
		st.goals["stability"].target_date = self.goal_stab_date.date().toString("yyyy-MM-dd")
		self.storage.save()
		self._refresh_goals_budgets()

	def _save_budgets(self) -> None:
		st = self.storage.state
		for c, spin in self.budget_inputs.items():
			st.budgets_monthly[c] = float(spin.value())
		self.storage.save()
		self._refresh_goals_budgets()

	def _export_csv(self) -> None:
		path, _ = QFileDialog.getSaveFileName(self, "Сохранить CSV", "history.csv", "CSV (*.csv)")
		if not path:
			return
		st = self.storage.state
		import csv
		with open(path, "w", newline="", encoding="utf-8") as f:
			writer = csv.writer(f)
			writer.writerow(["date", "type", "category", "amount", "note", "tags"])
			for t in st.history:
				writer.writerow([t.date, t.type, t.category or "", t.amount, t.note, ", ".join(t.tags or [])])
		QMessageBox.information(self, "Экспорт", "История экспортирована в CSV")

	def _export_excel(self) -> None:
		path, _ = QFileDialog.getSaveFileName(self, "Сохранить Excel", "finance.xlsx", "Excel (*.xlsx)")
		if not path:
			return
		st = self.storage.state
		df = pd.DataFrame([{
			"date": t.date,
			"type": t.type,
			"category": t.category or "",
			"amount": t.amount,
			"note": t.note or "",
			"tags": ", ".join(t.tags or []),
		} for t in st.history])
		if not df.empty:
			df_month = df.copy()
			df_month["date"] = df_month["date"].str[:7]
			df_month["signed"] = df_month.apply(lambda r: r["amount"] if r["type"] == "income" else -abs(r["amount"]), axis=1)
			monthly = df_month.groupby("date")["signed"].sum().reset_index(name="net")
		else:
			monthly = pd.DataFrame(columns=["date", "net"])
		budgets = pd.DataFrame([{
			"category": CATEGORY_LABELS_RU[c],
			"limit": self.storage.state.budgets_monthly.get(c, 0.0),
		} for c in CATEGORY_ORDER])
		with pd.ExcelWriter(path, engine="openpyxl") as writer:
			df.to_excel(writer, sheet_name="transactions", index=False)
			monthly.to_excel(writer, sheet_name="monthly", index=False)
			budgets.to_excel(writer, sheet_name="budgets", index=False)
		QMessageBox.information(self, "Экспорт", "Данные экспортированы в Excel")

	def _reset_data(self) -> None:
		ans = QMessageBox.question(self, "Сброс данных", "Удалить историю и обнулить балансы?", QMessageBox.Yes | QMessageBox.No)
		if ans == QMessageBox.Yes:
			self.storage.reset()
			self._refresh_all()
			QMessageBox.information(self, "Готово", "Данные сброшены")

	def _reset_settings(self) -> None:
		self.storage.state.settings["theme"] = "light"
		self.storage.state.settings["language"] = "ru"
		self.storage.state.settings["backup_enabled"] = "0"
		self.storage.state.settings["backup_interval_minutes"] = "60"
		self.storage.state.settings["backup_mode"] = "0"
		self.storage.save()
		self._apply_theme("light")
		if hasattr(self, "theme_combo"):
			self.theme_combo.setCurrentIndex(0)
		self.lang_combo.setCurrentIndex(0)
		self.backup_enable.setCurrentIndex(0)
		self.backup_mode.setCurrentIndex(0)
		self.backup_minutes.setValue(60)
		self._update_backup_controls_visibility()
		self._configure_backup_timer()
		QMessageBox.information(self, "Настройки", "Настройки сброшены к значениям по умолчанию.")

	def _auto_backup_tick(self) -> None:
		try:
			self._make_backup_impl(quiet=True)
		except Exception:
			pass

	def _build_help_tab(self) -> None:
		w = QWidget()
		layout = QVBoxLayout(w)
		help_text = QTextBrowser()
		help_text.setOpenExternalLinks(True)
		help_text.setHtml(
			"""
			<h2>Обучение — быстрый старт</h2>
			<ol>
				<li><b>Дашборд</b>: введите доход и нажмите «Рассчитать распределение и добавить доход» — средства разложатся по 50/25/15/10.</li>
				<li><b>Транзакции</b>: добавляйте расходы (категория, сумма, примечание, теги). Двойной клик — редактирование; ПКМ — редактировать или удалить.</li>
				<li><b>Графики</b>: смотрите динамику доходов/расходов, нетто и структуру расходов по категориям.</li>
				<li><b>Цели и бюджет</b>: задайте цели «Рост/Стабильность» и лимиты по категориям — следите за прогрессом и превышениями.</li>
				<li><b>Экспорт</b>: выгружайте CSV/Excel. Там же — бэкапы и восстановление.</li>
			</ol>
			<h3>Полезно</h3>
			<ul>
				<li>Добавить расход: заполните «Категория» и «Расход», затем нажмите кнопку или клавишу Enter при фокусе на сумме.</li>
				<li>Фильтры: используйте строки «поиск» и «тег» над таблицей, а также выбор месяца.</li>
				<li>Редактирование: двойной клик по строке или ПКМ → «Редактировать…».</li>
				<li>Совет: помечайте регулярные траты тегами (например, «подписка», «топливо») для быстрого поиска.</li>
			</ul>
			<h3>Проценты и прогнозы</h3>
			<ul>
				<li>Во вкладке «Вклад и прогнозы» посчитайте будущий баланс по простой или сложной ставке.</li>
				<li>Графики автоматически обновляются при добавлении/редактировании операций.</li>
			</ul>
			<h3>Где что хранится</h3>
			<ul>
				<li>Данные — в локальной базе <b>finance.db</b> (SQLite). Бэкапы — в папке <b>backups</b>.</li>
				<li>Настройки (тема, язык, автобэкап) сохраняются автоматически.</li>
			</ul>
			<p>Перед восстановлением из бэкапа закройте приложение на других устройствах/вкладках, чтобы избежать потери данных.</p>
			"""
		)
		layout.addWidget(help_text)
		idx = self.tabs.addTab(w, "Обучение 📘")
		try:
			self.tabs.tabBar().setTabTextColor(idx, QColor("#1e88e5"))
		except Exception:
			pass

	def _build_export_tab(self) -> None:
		w = QWidget()
		layout = QVBoxLayout(w)
		btn_csv = QPushButton("Экспортировать в CSV")
		btn_csv.clicked.connect(self._export_csv)
		btn_xlsx = QPushButton("Экспортировать в Excel (.xlsx)")
		btn_xlsx.clicked.connect(self._export_excel)
		layout.addWidget(QLabel("Экспорт данных"))
		layout.addWidget(btn_csv)
		layout.addWidget(btn_xlsx)

		layout.addWidget(QLabel("Резервные копии"))
		row = QHBoxLayout()
		btn_backup = QPushButton("Создать бэкап")
		btn_backup.clicked.connect(lambda: self._make_backup_impl(quiet=False))
		btn_restore = QPushButton("Восстановить из бэкапа…")
		btn_restore.clicked.connect(self._restore_backup)
		row.addWidget(btn_backup)
		row.addWidget(btn_restore)
		row.addStretch(1)
		layout.addLayout(row)
		layout.addStretch(1)
		self.tabs.addTab(w, "Экспорт")

	def _make_backup_impl(self, quiet: bool = False) -> None:
		base = Path(__file__).resolve().parent.parent
		db = base / "finance.db"
		if not db.exists():
			if not quiet:
				QMessageBox.warning(self, "Бэкап", "Файл базы не найден.")
			return
		backup_dir = base / "backups"
		backup_dir.mkdir(exist_ok=True)
		from datetime import datetime
		name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
		dst = backup_dir / name
		import shutil
		shutil.copy2(db, dst)
		files = sorted(backup_dir.glob("backup_*.db"))
		if len(files) > 10:
			for f in files[:-10]:
				try:
					f.unlink()
				except Exception:
					pass
		if not quiet:
			QMessageBox.information(self, "Бэкап", f"Создан бэкап: {dst.name}")
