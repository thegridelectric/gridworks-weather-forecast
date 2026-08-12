"""gridworks-weather DB — gwwf's own database; gwwf is the sole accessor."""

from gwwf.db.models import Base
from gwwf.db.session import SessionLocal

__all__ = ["Base", "SessionLocal"]
