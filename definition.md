# Goal 
-   A hermes agent poll/listen telemetry logs from remote servers then use use jev and llm to decide to fire events sending messages through channels to user with predefined types of log events (warn/error/fatal/panic).

# Objects:
    - hermes: https://hermes-agent.nousresearch.com/
    - jev: 
        - https://docs.typesafe.ai/introduction
        - https://openrouter.ai/docs/guides/community/jev (Use OpenRouter to provide Jev API Key)
    - llm: https://ai.google.dev/gemini-api/docs/api-key

# Requirement
- Define an architecture that flexible to switch provider, and extensible.
- The agent will not just forward logs to the user, it's also need to though about the issue logs and give advise.
