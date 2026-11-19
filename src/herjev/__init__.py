"""herjev — a log-watch agent.

Poll/listen to remote telemetry logs → detect warn/error/fatal/panic →
ask Jev whether (and how urgently) to alert → ask an LLM for root-cause
analysis and advice → deliver through channels (Telegram, Slack, Discord,
webhooks to other agents, ...).
"""

__version__ = "0.1.0"
