class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, sku, qty):
        self.items[sku] = qty

    def remove(self, sku, qty):
        self.items[sku] -= qty

    def quantity(self, sku):
        return self.items[sku]
