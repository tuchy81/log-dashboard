import os
from datetime import date

AS_OF = date.fromisoformat(os.environ.get("AS_OF", "2026-10-12"))
