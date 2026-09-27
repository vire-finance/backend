import uuid

# dummy
class CurrentUser:
    def __init__(self):
        self.id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        self.role = "OWNER"

def get_current_user():
    return CurrentUser()