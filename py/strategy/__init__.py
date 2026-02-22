"""Strategy package — structured AI agent strategy documents.

Strategy Document → prompt compiler → LLM system prompt.
Users define strategies through archetypes, sliders, and text fields;
the compiler converts them into XML-sectioned system prompts.
"""

from strategy.schema import Strategy
from strategy.store import StrategyStore

__all__ = ["Strategy", "StrategyStore"]
