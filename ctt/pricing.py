"""Model prices (USD per 1M tokens) and request cost."""
from dataclasses import dataclass

from ctt.config import load_layered


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float
    assumed: bool = False


class PriceTable:
    def __init__(self, data):
        self.models = {name: Price(**spec) for name, spec in data.get("models", {}).items()}
        self.fallback = data.get("fallback", {}).get("model")
        if self.fallback not in self.models:
            raise ValueError(f"fallback model '{self.fallback}' is not in [models]")
        self._by_length = sorted(self.models, key=len, reverse=True)

    def lookup(self, model):
        """Longest model-id prefix wins (so dated ids like claude-haiku-4-5-20251001 match). Returns (price, known)."""
        for name in self._by_length:
            if model and model.startswith(name):
                return self.models[name], True
        return self.models[self.fallback], False

    def cost(self, request):
        price, known = self.lookup(request.model)
        usd = (request.input_tokens * price.input
               + request.output_tokens * price.output
               + request.cache_read * price.cache_read
               + request.cache_write_5m * price.cache_write_5m
               + request.cache_write_1h * price.cache_write_1h) / 1e6
        return usd, known


def load_prices(explicit=None):
    return PriceTable(load_layered("prices.toml", explicit))
