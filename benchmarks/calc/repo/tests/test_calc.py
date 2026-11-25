from calc.ops import add, mean, median
from calc.parse import parse_numbers


def test_add():
    assert add(2, 3) == 5


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_parse_numbers():
    assert parse_numbers("1, 2.5,3") == [1.0, 2.5, 3.0]


def test_parse_empty():
    assert parse_numbers("") == []
