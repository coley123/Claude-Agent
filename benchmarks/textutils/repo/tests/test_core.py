from textutils.core import slugify, truncate, word_count


def test_slugify():
    assert slugify("Hello, World!  Again") == "hello-world-again"


def test_word_count():
    assert word_count("The cat. the CAT!") == {"the": 2, "cat": 2}


def test_truncate_short():
    assert truncate("abc", 5) == "abc"


def test_truncate_long():
    out = truncate("abcdefghij", 6)
    assert out == "abc..." and len(out) == 6
