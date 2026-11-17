from __future__ import annotations

import pytest
from pydantic import ValidationError

from ossie_studio.config import Settings


def test_session_secret_trop_court_refuse() -> None:
    with pytest.raises(ValidationError):
        Settings(session_secret="court", _env_file=None)  # type: ignore[call-arg]
