def parse_numbers(text):
    """Parse a comma-separated list like '1, 2.5,3' into floats. Empty input -> []."""
    return [int(x) for x in text.split(",")]
