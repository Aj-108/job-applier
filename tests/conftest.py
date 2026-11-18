import pytest


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    """Tests must never call a real AI (your .env key would be used and quota spent). Tests that need an AI mock it."""
    monkeypatch.setenv("AI_PROVIDER", "none")


@pytest.fixture(autouse=True)
def isolated_excel(monkeypatch, tmp_path):
    """Tests must never read or write the real Applied_Jobs.xlsx (or its backup)."""
    from core import excel
    monkeypatch.setattr(excel, "excel_path", lambda: tmp_path / "Applied_Jobs.xlsx")
    monkeypatch.setattr(excel, "OUT", tmp_path / "out")
