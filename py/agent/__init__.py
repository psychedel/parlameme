"""Agent package — server-side LLM agent runner.

Runs AI agents with compiled strategies in game sessions.
Uses InProcessBridge to call MCPServer directly (no HTTP overhead).
"""

from agent.bots import BotRunner
from agent.bridge import InProcessBridge
from agent.runner import AgentRunner

__all__ = ["AgentRunner", "BotRunner", "InProcessBridge"]
