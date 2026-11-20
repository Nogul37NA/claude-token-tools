"""Model prices (USD per 1M tokens) and request cost."""
from dataclasses import dataclass, replace

from ctt.config import load_layered


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float
    assumed: bool = False
    # Second rate card for long prompts (e.g. Haiku 5.5 above 100K): a prompt (input + cache read + cache write)
    # over long_above tokens pays long_input / long_output, and the cache rates scale by long_input / input.
    long_above: int = 0
    long_input: float = 0.0
    long_output: float = 0.0

    def at(self, prompt_tokens):
        """The rate card that applies to a request whose prompt is prompt_tokens long."""
        if not self.long_above or prompt_tokens <= self.long_above or not self.input:
            return self
        scale = self.long_input / self.input
        return replace(self, input=self.long_input, output=self.long_output, cache_read=self.cache_read * scale,
                       cache_write_5m=self.cache_write_5m * scale, cache_write_1h=self.cache_write_1h * scale,
                       long_above=0)


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
        price = price.at(request.input_tokens + request.cache_read + request.cache_write_5m + request.cache_write_1h)
        usd = (request.input_tokens * price.input
               + request.output_tokens * price.output
               + request.cache_read * price.cache_read
               + request.cache_write_5m * price.cache_write_5m
               + request.cache_write_1h * price.cache_write_1h) / 1e6
        return usd, known


def load_prices(explicit=None):
    return PriceTable(load_layered("prices.toml", explicit))
