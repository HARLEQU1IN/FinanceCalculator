from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple

Category = Literal["essentials", "growth", "stability", "rewards"]


CATEGORY_LABELS_RU: Dict[Category, str] = {
	"essentials": "Обязательные (50%)",
	"growth": "Накопления рост (25%)",
	"stability": "Подушка стабильности (15%)",
	"rewards": "Вознаграждения (10%)",
}

CATEGORY_ORDER: List[Category] = [
	"essentials",
	"growth",
	"stability",
	"rewards",
]

ALLOCATION_PCT: Dict[Category, float] = {
	"essentials": 0.50,
	"growth": 0.25,
	"stability": 0.15,
	"rewards": 0.10,
}


@dataclass
class Transaction:
	date: str
	type: Literal["income", "expense"]
	category: Optional[Category]
	amount: float
	note: str = ""
	tags: List[str] = field(default_factory=list)


@dataclass
class Goal:
	target_amount: float = 0.0
	target_date: str = ""  # YYYY-MM-DD


@dataclass
class FinanceState:
	balances: Dict[Category, float] = field(default_factory=lambda: {c: 0.0 for c in CATEGORY_ORDER})
	history: List[Transaction] = field(default_factory=list)
	base_monthly_expenses: float = 0.0
	goals: Dict[str, Goal] = field(default_factory=lambda: {
		"growth": Goal(),
		"stability": Goal(),
	})
	budgets_monthly: Dict[Category, float] = field(default_factory=lambda: {c: 0.0 for c in CATEGORY_ORDER})
	settings: Dict[str, str] = field(default_factory=lambda: {"theme": "light", "language": "ru"})

	def to_dict(self) -> dict:
		return {
			"balances": self.balances,
			"history": [
				{**t.__dict__, "tags": list(t.tags) if t.tags else []}
				for t in self.history
			],
			"base_monthly_expenses": self.base_monthly_expenses,
			"goals": {k: v.__dict__ for k, v in self.goals.items()},
			"budgets_monthly": self.budgets_monthly,
			"settings": self.settings,
		}

	@staticmethod
	def from_dict(data: dict) -> "FinanceState":
		state = FinanceState()
		state.balances.update(data.get("balances", {}))
		state.history = [
			Transaction(
				date=t.get("date", ""),
				type=t.get("type", "expense"),
				category=t.get("category"),
				amount=float(t.get("amount", 0.0)),
				note=t.get("note", ""),
				tags=list(t.get("tags", [])),
			)
			for t in data.get("history", [])
		]
		state.base_monthly_expenses = float(data.get("base_monthly_expenses", 0.0))
		g = data.get("goals", {})
		for k in ["growth", "stability"]:
			if k in g:
				goal_data = g[k]
				state.goals[k] = Goal(
					target_amount=float(goal_data.get("target_amount", 0.0)),
					target_date=str(goal_data.get("target_date", "")),
				)
		state.budgets_monthly.update({
			c: float(v) for c, v in data.get("budgets_monthly", {}).items()
		})
		state.settings.update({k: str(v) for k, v in data.get("settings", {}).items()})
		return state


def split_income(amount: float) -> Dict[Category, float]:
	return {cat: round(amount * pct, 2) for cat, pct in ALLOCATION_PCT.items()}


def add_income(state: FinanceState, amount: float, note: str = "", tags: Optional[List[str]] = None) -> Dict[Category, float]:
	alloc = split_income(amount)
	for cat, val in alloc.items():
		state.balances[cat] = round(state.balances.get(cat, 0.0) + val, 2)
	state.history.append(Transaction(
		date=datetime.now().strftime("%Y-%m-%d %H:%M"),
		type="income",
		category=None,
		amount=round(amount, 2),
		note=note or "Доход",
		tags=list(tags or []),
	))
	return alloc


def add_expense(state: FinanceState, category: Category, amount: float, note: str = "", tags: Optional[List[str]] = None) -> None:
	amount = round(amount, 2)
	state.balances[category] = round(state.balances.get(category, 0.0) - amount, 2)
	state.history.append(Transaction(
		date=datetime.now().strftime("%Y-%m-%d %H:%M"),
		type="expense",
		category=category,
		amount=amount,
		note=note or "Расход",
		tags=list(tags or []),
	))


def percent_breakdown(amount: float) -> Dict[Category, Dict[str, float]]:
	alloc = split_income(amount)
	return {
		cat: {"percent": round(ALLOCATION_PCT[cat] * 100, 2), "amount": val}
		for cat, val in alloc.items()
	}


def totals_to_accounts_and_spend(amount: float, actual_essentials_spent: float) -> Dict[str, float]:
	alloc = split_income(amount)
	savings = alloc["growth"] + alloc["stability"]
	planned_spend = alloc["essentials"] + alloc["rewards"]
	return {
		"to_savings": round(savings, 2),
		"to_spend_planned": round(planned_spend, 2),
		"overspend_warning": float(actual_essentials_spent > alloc["essentials"]),
	}


def future_value_simple(principal: float, annual_rate_pct: float, years: float) -> float:
	r = annual_rate_pct / 100.0
	return round(principal * (1 + r * years), 2)


def future_value_compound(principal: float, annual_rate_pct: float, years: float, compounds_per_year: int = 12) -> float:
	r = annual_rate_pct / 100.0
	return round(principal * (1 + r / compounds_per_year) ** (compounds_per_year * years), 2)


def projection_monthly_contrib(start: float, monthly_contrib: float, annual_rate_pct: float, months: int, compound: bool = True) -> float:
	r = annual_rate_pct / 100.0
	balance = start
	for _ in range(months):
		if compound:
			balance *= (1 + r / 12)
		else:
			balance += balance * (r / 12)
		balance += monthly_contrib
	return round(balance, 2)


# Helpers for goals/budgets/filters

def goal_progress(current: float, target_amount: float) -> float:
	if target_amount <= 0:
		return 0.0
	return max(0.0, min(100.0, round(current / target_amount * 100.0, 2)))


def month_key(dt_str: str) -> str:
	# expects YYYY-MM-DD HH:MM
	return (dt_str or "")[:7]


def monthly_category_spent(state: FinanceState, month: str) -> Dict[Category, float]:
	spent: Dict[Category, float] = {c: 0.0 for c in CATEGORY_ORDER}
	for t in state.history:
		if t.type == "expense" and month_key(t.date) == month and t.category:
			spent[t.category] = round(spent[t.category] + t.amount, 2)
	return spent


def filter_transactions(state: FinanceState, text: str = "", tag: str = "", month: str = "") -> List[Transaction]:
	q = (text or "").lower().strip()
	tag = (tag or "").lower().strip()
	month = (month or "").strip()
	res: List[Transaction] = []
	for t in state.history:
		if month and month_key(t.date) != month:
			continue
		if q and q not in (t.note or "").lower() and (t.category or "") not in q:
			continue
		if tag:
			lower_tags = [s.lower() for s in (t.tags or [])]
			if tag not in lower_tags:
				continue
		res.append(t)
	return res
