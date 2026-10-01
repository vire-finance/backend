from app.api.payment.model import InvoicePayment, TopUp
from app.shared.repository import Repository

class PaymentRepository(Repository):
    model = TopUp

    def by_key(self, owner_id, key):
        return self.one(
            TopUp.owner_id == owner_id,
            TopUp.idempotency_key == key,
        )

    def for_owner(self, owner_id):
        return self.rows(TopUp.owner_id == owner_id)

class InvoiceRepository(Repository):
    model = InvoicePayment

    def for_owner(self, owner_id):
        return self.rows(InvoicePayment.owner_id == owner_id)