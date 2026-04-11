import logging


def after_func(retry_state) -> None:
    """Tenacity after-callback used across agents/tools for uniform retry logs."""
    exc = None
    if retry_state is not None and retry_state.outcome is not None:
        exc = retry_state.outcome.exception()

    if exc is None:
        logging.warning("Retrying operation (attempt %s)", retry_state.attempt_number if retry_state else "?")
    else:
        logging.warning(
            "Retrying operation (attempt %s) due to: %s",
            retry_state.attempt_number if retry_state else "?",
            exc,
        )
