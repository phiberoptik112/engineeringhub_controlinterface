"""Custom exceptions for Engineering Hub."""


class HubError(Exception):
    """Base exception for Engineering Hub errors."""

    pass


class DjangoAPIError(HubError):
    """Error communicating with Django backend API."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotesParseError(HubError):
    """Error parsing shared notes file."""

    def __init__(self, message: str, line_number: int | None = None) -> None:
        super().__init__(message)
        self.line_number = line_number


class AgentExecutionError(HubError):
    """Error during agent execution."""

    def __init__(self, message: str, agent_type: str | None = None) -> None:
        super().__init__(message)
        self.agent_type = agent_type


class LLMBackendError(HubError):
    """Provider-agnostic error raised by LLM backends (Anthropic, MLX, etc.)."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class ConfigurationError(HubError):
    """Error in configuration."""

    pass
