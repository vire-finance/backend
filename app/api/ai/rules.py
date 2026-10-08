"""Transparent attention rules; thresholds are configurable and never assert fraud."""
from app.api.ai.schema import FinancialSignal, OverallStatus, Severity
from app.core.config import settings


class FinancialRules:
    def __init__(self):
        self.increase = settings.AI_SPENDING_INCREASE_PERCENT
        self.utilization = settings.AI_HIGH_UTILIZATION_PERCENT
        self.concentration = settings.AI_CONCENTRATION_PERCENT
        self.large_multiplier = settings.AI_LARGE_TRANSACTION_MULTIPLIER
        self.min_baseline = settings.AI_MIN_BASELINE_TRANSACTIONS

    @staticmethod
    def overall_status(signals):
        if any(s.severity == Severity.WARNING for s in signals):
            return OverallStatus.WARNING
        if any(s.severity == Severity.ATTENTION for s in signals):
            return OverallStatus.ATTENTION
        return OverallStatus.NORMAL

    def evaluate(self, summary, budget, pockets, cards, categories, large, availability):
        signals = []

        def add(rule, title, description, action, current, ref, *, entity_type="business",
                entity_id=None, name=None, previous=None, change=None, threshold=None,
                severity=Severity.ATTENTION):
            signals.append(FinancialSignal(id=f"{rule}:{entity_id or ref}", rule=rule,
                severity=severity, title=title, description=description, recommended_action=action,
                entity_type=entity_type, entity_id=entity_id, entity_name=name, current_value=current,
                previous_value=previous, change_percentage=change, threshold=threshold, evidence_refs=[ref]))

        if summary.change_percentage is not None and summary.change_percentage >= self.increase:
            add("SPENDING_INCREASE", "Pengeluaran meningkat", "Pengeluaran melebihi ambang kenaikan terhadap periode sebelumnya.",
                "Tinjau Pocket dan transaksi dengan pengeluaran terbesar; pertimbangkan jumlah hari periode yang sudah berjalan.",
                summary.total_spending, "summary", previous=summary.previous_period_spending,
                change=summary.change_percentage, threshold=self.increase, severity=Severity.WARNING)
        if budget.current_month_limit_utilization_percentage is not None and budget.current_month_limit_utilization_percentage >= self.utilization:
            add("MONTHLY_LIMIT_UTILIZATION", "Pemakaian limit bulanan tinggi",
                "Pengeluaran bulan kalender berjalan mendekati atau mencapai total limit Pocket saat ini.",
                "Prioritaskan kebutuhan tersisa untuk bulan berjalan.", budget.current_month_spent,
                "budget", threshold=self.utilization, previous=budget.monthly_limit, severity=Severity.WARNING)
        for rows, kind in ((pockets, "pocket"), (cards, "card")):
            for row in rows:
                ref = f"{kind}:{row.id}"
                if row.change_percentage is not None and row.change_percentage >= self.increase:
                    add(f"{kind.upper()}_SPENDING_INCREASE", "Pengeluaran meningkat", "Pengeluaran entitas meningkat terhadap periode sebelumnya.",
                        "Tinjau transaksi terbesar pada entitas ini.", row.amount, ref,
                        entity_type=kind, entity_id=row.id, name=row.name,
                        previous=row.previous_period_amount, change=row.change_percentage, threshold=self.increase)
                if row.current_month_limit_utilization_percentage >= self.utilization:
                    add(f"{kind.upper()}_LIMIT_UTILIZATION", "Pemakaian limit mendekati batas",
                        "Pemakaian limit bulan kalender berjalan melampaui ambang perhatian.",
                        "Tinjau kebutuhan pengeluaran sebelum limit habis.", row.current_month_spent, ref,
                        entity_type=kind, entity_id=row.id, name=row.name,
                        previous=row.current_monthly_limit, threshold=self.utilization, severity=Severity.WARNING)
                if kind == "pocket" and row.current_budget_utilization_percentage >= self.utilization:
                    add("POCKET_AVAILABLE_BUDGET_LOW", "Sisa dana Pocket menipis", "Sisa dana Pocket saat ini berada di bawah ambang perhatian.",
                        "Periksa sisa dana dan kebutuhan operasional berikutnya.", row.current_remaining_amount, ref,
                        entity_type=kind, entity_id=row.id, name=row.name,
                        previous=row.current_allocated_amount, threshold=self.utilization)
        active_cards = [r for r in cards if r.amount > 0]
        if len(active_cards) >= 2:
            for row in active_cards:
                if row.percentage >= self.concentration:
                    add("CARD_SPENDING_CONCENTRATION", "Pengeluaran terkonsentrasi pada Card",
                        "Kontribusi Card terhadap pengeluaran periode melampaui ambang konsentrasi.",
                        "Tinjau kebutuhan dan transaksi Card dengan pengeluaran terbesar.", row.amount,
                        f"card:{row.id}", entity_type="card", entity_id=row.id, name=row.name,
                        threshold=self.concentration)
        active_categories = [r for r in categories if r.amount > 0]
        if len(active_categories) >= 2:
            for row in active_categories:
                if row.percentage >= self.concentration:
                    add("CATEGORY_SPENDING_CONCENTRATION", "Pengeluaran terkonsentrasi pada kategori",
                        "Proporsi pengeluaran kategori melampaui ambang konsentrasi; ini bukan bukti penyalahgunaan.",
                        "Tinjau apakah konsentrasi sesuai rencana operasional.", row.amount,
                        f"category:{row.category if row.category is not None else '<uncategorized>'}",
                        entity_type="category", name=row.label, threshold=self.concentration)
        for row in large:
            add("LARGE_TRANSACTION", "Transaksi berpotensi tidak biasa",
                "Nominal melebihi kelipatan rata-rata transaksi pada tiga bulan kalender sebelum awal periode.",
                "Tinjau bukti dan konteks transaksi secara manual.", int(row["amount"]), f"transaction:{row['id']}",
                entity_type="transaction", entity_id=row["id"], name=row["card_name"],
                previous=availability.baseline_average_transaction_amount,
                threshold=self.large_multiplier, severity=Severity.WARNING)
        if availability.approved_transactions_missing_processed_at:
            add("MISSING_TRANSACTION_DATE", "Tanggal ledger perlu diperiksa",
                "Transaksi APPROVED tanpa processed_at dikecualikan dari seluruh metrik periode.",
                "Rekonsiliasi tanggal transaksi sebelum menilai laporan.",
                availability.approved_transactions_missing_processed_at, "data_availability",
                severity=Severity.WARNING)
        return sorted(signals, key=lambda s: (0 if s.severity == Severity.WARNING else 1, s.id))
