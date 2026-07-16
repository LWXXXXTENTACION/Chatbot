"""Build bounded model input without mutating persisted conversation state."""

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """A token-budgeted view of the persisted LangGraph message history."""

    messages: list[BaseMessage]
    estimated_tokens: int
    original_tokens: int
    dropped_messages: int
    overflowed: bool


def _count_tokens(messages: list[BaseMessage]) -> int:
    return count_tokens_approximately(messages)


def _complete_turns(messages: list[BaseMessage]) -> tuple[list[BaseMessage], list[list[BaseMessage]]]:
    """Split history into complete user turns while retaining any legacy prefix.

    A turn starts with a HumanMessage and contains every following assistant and
    tool message up to the next HumanMessage. Keeping the whole group prevents a
    ToolMessage from being separated from the AI tool call that created it.
    """
    prefix: list[BaseMessage] = []
    turns: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []

    for message in messages:
        if isinstance(message, HumanMessage):
            if current:
                turns.append(current)
            current = [message]
        elif current:
            current.append(message)
        else:
            prefix.append(message)

    if current:
        turns.append(current)
    return prefix, turns


def build_context_window(
    system_messages: list[BaseMessage],
    history_messages: list[BaseMessage],
    *,
    max_tokens: int,
) -> ContextWindow:
    """Return pinned system prompts plus the newest complete turns that fit.

    This function never edits ``history_messages``. The newest turn is always
    retained so the model can answer the current request, even when that single
    turn is larger than the configured budget. ``overflowed`` exposes that rare
    case for logging and future model-specific handling.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")

    full_input = [*system_messages, *history_messages]
    original_tokens = _count_tokens(full_input)
    if original_tokens <= max_tokens:
        return ContextWindow(
            messages=full_input,
            estimated_tokens=original_tokens,
            original_tokens=original_tokens,
            dropped_messages=0,
            overflowed=False,
        )

    prefix, turns = _complete_turns(history_messages)
    selected_reversed: list[list[BaseMessage]] = []
    selected_tokens = _count_tokens(system_messages)

    for turn in reversed(turns):
        turn_tokens = _count_tokens(turn)
        if not selected_reversed:
            selected_reversed.append(turn)
            selected_tokens += turn_tokens
            continue
        if selected_tokens + turn_tokens > max_tokens:
            break
        selected_reversed.append(turn)
        selected_tokens += turn_tokens

    selected_turns = list(reversed(selected_reversed))
    selected_history = [message for turn in selected_turns for message in turn]

    # Prefix messages only occur in legacy/malformed histories. Preserve them
    # when they fit, but never evict a valid recent user turn for them.
    prefix_tokens = _count_tokens(prefix)
    if prefix and selected_tokens + prefix_tokens <= max_tokens:
        selected_history = [*prefix, *selected_history]

    bounded_messages = [*system_messages, *selected_history]
    estimated_tokens = _count_tokens(bounded_messages)
    return ContextWindow(
        messages=bounded_messages,
        estimated_tokens=estimated_tokens,
        original_tokens=original_tokens,
        dropped_messages=len(history_messages) - len(selected_history),
        overflowed=estimated_tokens > max_tokens,
    )
