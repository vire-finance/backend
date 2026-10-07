"""Typed owner analytics and strictly validated interpretation contracts."""
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Severity(str, Enum):
    INFO = "info"
    ATTENTION = "attention"
    WARNING = "warning"


class OverallStatus(str, Enum):
    NORMAL = "normal"
    ATTENTION = "attention"
    WARNING = "warning"


class PeriodWindow(StrictSchema):
    start: datetime
    end: datetime
    label: str
    timezone: Literal["Asia/Jakarta"] = "Asia/Jakarta"
    end_exclusive: Literal[True] = True


class SpendingSummary(StrictSchema):
    total_spending: int
    previous_period_spending: int
    absolute_difference: int
    change_percentage: float | None
    comparison_status: Literal["comparable", "no_previous_spending"]
    transaction_count: int
    previous_transaction_count: int
    average_transaction_amount: float


class TrendPoint(StrictSchema):
    period: str
    amount: int
    transaction_count: int


class SpendingBreakdown(StrictSchema):
    amount: int
    percentage: float
    previous_period_amount: int
    change_percentage: float | None
    transaction_count: int


class PocketBreakdown(SpendingBreakdown):
    id: UUID
    name: str
    current_allocated_amount: int
    current_remaining_amount: int
    current_budget_utilization_percentage: float
    current_monthly_limit: int
    current_month_spent: int
    current_month_remaining_limit: int
    current_month_limit_utilization_percentage: float


class CardBreakdown(SpendingBreakdown):
    id: UUID
    pocket_id: UUID
    name: str
    category: str | None
    current_balance: int
    current_monthly_limit: int
    current_month_spent: int
    current_month_remaining_limit: int
    current_month_limit_utilization_percentage: float


class CategoryBreakdown(SpendingBreakdown):
    category: str | None
    label: str


class BudgetSnapshot(StrictSchema):
    as_of: datetime
    scope: Literal["current_snapshot"] = "current_snapshot"
    allocated_amount: int
    used_amount: int
    remaining_amount: int
    utilization_percentage: float | None
    current_limit_period: str
    monthly_limit: int
    current_month_spent: int
    current_month_remaining_limit: int
    current_month_limit_utilization_percentage: float | None


class TopTransaction(StrictSchema):
    id: UUID
    amount: int
    processed_at: datetime
    pocket_id: UUID
    pocket_name: str
    card_id: UUID
    card_name: str


class Ranking(StrictSchema):
    id: UUID | None = None
    name: str
    amount: int


class TopSpending(StrictSchema):
    pockets: list[Ranking]
    cards: list[Ranking]
    categories: list[Ranking]
    transactions: list[TopTransaction]


class FinancialSignal(StrictSchema):
    id: str
    rule: str
    severity: Severity
    title: str
    description: str
    recommended_action: str
    entity_type: Literal["business", "pocket", "card", "category", "transaction"]
    entity_id: UUID | None = None
    entity_name: str | None = None
    current_value: int | float
    previous_value: int | float | None = None
    change_percentage: float | None = None
    threshold: float | None = None
    evidence_refs: list[str]


class DataAvailability(StrictSchema):
    department_breakdown_supported: Literal[False] = False
    department_reason: str = "No department/division model or employee relationship exists."
    merchant_breakdown_supported: Literal[False] = False
    merchant_reason: str = "Party names exist only on fund requests; direct card payments have no merchant field."
    historical_budget_supported: Literal[False] = False
    historical_budget_reason: str = "Budgets, balances and monthly limits store current values, not historical snapshots."
    category_basis: str = "Current Card.category; past category assignments are not recorded."
    approved_transactions_missing_processed_at: int
    baseline_transaction_count: int
    baseline_average_transaction_amount: float | None
    baseline_history_sufficient: bool


class AnalyticsResponse(StrictSchema):
    currency: Literal["IDR"] = "IDR"
    is_simulated: Literal[True] = True
    spending_basis: Literal["APPROVED transactions, processed_at"] = "APPROVED transactions, processed_at"
    period: PeriodWindow
    previous_period: PeriodWindow
    summary: SpendingSummary
    monthly_trend: list[TrendPoint]
    department_breakdown: list[dict] = Field(default_factory=list, max_length=0)
    pocket_breakdown: list[PocketBreakdown]
    card_breakdown: list[CardBreakdown]
    category_breakdown: list[CategoryBreakdown]
    budget: BudgetSnapshot
    top_spending: TopSpending
    signals: list[FinancialSignal]
    overall_status: OverallStatus
    data_availability: DataAvailability


class InsightType(str, Enum):
    SPENDING_TREND = "spending_trend"
    BUDGET = "budget"
    CONCENTRATION = "concentration"
    LARGE_TRANSACTION = "large_transaction"
    DATA_QUALITY = "data_quality"
    GENERAL = "general"


class FinancialInsight(StrictSchema):
    type: InsightType
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1200)
    recommended_action: str = Field(min_length=1, max_length=600)
    # References must identify evidence actually supplied to the model.
    evidence_refs: list[str] = Field(min_length=1, max_length=5)


class FinancialNarrative(StrictSchema):
    overall_status: OverallStatus
    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    insights: list[FinancialInsight] = Field(min_length=1, max_length=6)


class AIAnalysis(StrictSchema):
    available: bool = False
    overall_status: OverallStatus | None = None
    headline: str | None = None
    summary: str | None = None
    insights: list[FinancialInsight] = Field(default_factory=list)
    fallback_reason: str | None = None
    generated_at: datetime | None = None
    expires_at: datetime | None = None
    cached: bool = False
    provider: str | None = None
    model: str | None = None


class OwnerInsightsResponse(AnalyticsResponse):
    ai_analysis: AIAnalysis
