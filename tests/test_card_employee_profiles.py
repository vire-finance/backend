from app.api.auth.model import User
from app.api.card.service import CardService
from app.shared.enums import UserRole
from test_main_funding import ctx, settings_card


def test_card_access_returns_selected_employee_profile_and_remove_revokes_access(ctx, settings_card):
    employee = User(email='profile@example.test', username='rida', full_name='Rida Employee', avatar_url='https://example.test/rida.png', phone_number='0800000001', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(employee)
    ctx.db.flush()
    service = CardService(ctx)
    service.access(settings_card, employee.id, True)
    selected = service.detail(settings_card)['employees'][0]
    assert selected['id'] == employee.id
    assert selected['full_name'] == 'Rida Employee'
    assert selected['avatar_url'] == employee.avatar_url
    assert selected['direct_access'] is True
    service.access(settings_card, employee.id, False)
    assert service.detail(settings_card)['employees'] == []
