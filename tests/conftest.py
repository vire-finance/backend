"""HTTP fixtures override request sessions; lifespan workers need isolation too."""
import asyncio
import pytest


@pytest.fixture(autouse=True)
def isolate_application_workers(monkeypatch):
    import app.main
    async def idle_worker():
        await asyncio.Event().wait()
    monkeypatch.setattr(app.main, "run_worker", idle_worker)
    monkeypatch.setattr(app.main, "run_ocr_worker", idle_worker)
