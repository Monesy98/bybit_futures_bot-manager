class RiskManager:
    def __init__(self, config):
        self.max_position_size = config.get('max_position_size', 0.01)

    def validate_order(self, symbol, side, amount):
        return amount <= self.max_position_size