from __future__ import annotations

import pytest

from app.ratelimit import AnalyzeGate, ModelBusy, RateLimited


def test_one_request_at_a_time():
    gate = AnalyzeGate(per_ip_per_hour=10)
    with gate.slot("1.1.1.1"):
        with pytest.raises(ModelBusy):
            with gate.slot("2.2.2.2"):
                pass
    with gate.slot("2.2.2.2"):  # после завершения слот снова свободен
        pass


def test_per_ip_limit():
    gate = AnalyzeGate(per_ip_per_hour=2)
    for _ in range(2):
        with gate.slot("1.1.1.1"):
            pass
    with pytest.raises(RateLimited) as e:
        with gate.slot("1.1.1.1"):
            pass
    assert 0 < e.value.retry_after_s <= 3600
    with gate.slot("2.2.2.2"):  # другой адрес не затронут
        pass


def test_slot_released_after_error():
    gate = AnalyzeGate(per_ip_per_hour=10)
    with pytest.raises(ValueError):
        with gate.slot("1.1.1.1"):
            raise ValueError
    with gate.slot("1.1.1.1"):
        pass
