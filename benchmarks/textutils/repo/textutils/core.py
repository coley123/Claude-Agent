def slugify(text):
    return text.replace(" ", "-")


def word_count(text):
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return counts


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
