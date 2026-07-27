import pandas as pd

from scripts.parsers.users_parser import _normalize_id as users_normalize_id
from scripts.parsers.oed_parser import _normalize_id as oed_normalize_id
from scripts.parsers.learning_parser import _norm_id as learning_norm_id
from scripts.parsers.attestations_parser import _norm_id as attestations_norm_id


def test_id_normalizers_strip_and_uppercase():
    assert users_normalize_id(" ab-12 ") == "AB-12"
    assert oed_normalize_id(" ab-12 ") == "AB-12"
    assert learning_norm_id(" ab-12 ") == "AB-12"
    assert attestations_norm_id(" ab-12 ") == "AB-12"


def test_id_normalizers_disagree_on_missing_value():
    """Задокументированное различие (см. аудит): большинство парсеров
    возвращают "" для пустого ID, attestations_parser — None. Это разное
    поведение под одинаковым названием функции — источник потенциальных
    багов, если кто-то унифицирует одну реализацию не заметив другую."""
    assert users_normalize_id(None) == ""
    assert oed_normalize_id(None) == ""
    assert learning_norm_id(None) == ""
    assert attestations_norm_id(None) is None
