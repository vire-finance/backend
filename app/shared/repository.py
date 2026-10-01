from sqlalchemy import select

class Repository:
    model = None

    def __init__(self, db):
        self.db = db

    def get(self, object_id):
        return self.db.get(self.model, object_id)

    def rows(self, *conditions):
        return self.db.scalars(select(self.model).where(*conditions)).all()

    def one(self, *conditions):
        return self.db.scalar(select(self.model).where(*conditions))

    def add(self, row):
        self.db.add(row)
        self.db.flush()
        return row

    def delete(self, row):
        self.db.delete(row)