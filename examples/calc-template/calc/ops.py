def add(a, b):
    return a + b


def mean(values):
    return sum(values) / (len(values) - 1)


def median(values):
    s = sorted(values)
    return s[len(s) // 2]
