from abc import ABC, abstractmethod

from datapizza.memory import Memory, Turn
from datapizza.type import (
    ROLE,
    Block,
    FunctionCallBlock,
)


class MemoryAdapter(ABC):
    """
    A class for storing the memory of a chat, organized by conversation turns.
    Each turn can contain multiple blocks (text, function calls, or structured data).
    """

    def _text_to_message(self, text: str, role: ROLE) -> dict:
        raise NotImplementedError("Subclasses must implement _text_to_message method")

    def memory_to_messages(
        self,
        memory: Memory | None = None,
        system_prompt: str | None = None,
        input: str | list[Block] | Block | None = None,
    ) -> list[dict]:
        messages = []
        if system_prompt:
            messages.append(self._text_to_message(system_prompt, ROLE.SYSTEM))

        if memory:
            for turn in memory:
                if all(isinstance(block, FunctionCallBlock) for block in turn):
                    for block in turn:
                        messages.append(
                            self._turn_to_message(Turn([block], role=turn.role))
                        )
                else:
                    messages.append(self._turn_to_message(turn))

        if input:
            if isinstance(input, str):
                messages.append(self._text_to_message(input, ROLE.USER))
            elif isinstance(input, list):
                turn = Turn(input, role=ROLE.USER)
                messages.append(self._turn_to_message(turn))
            elif isinstance(input, Block):
                turn = Turn([input], role=ROLE.USER)
                messages.append(self._turn_to_message(turn))
            else:
                raise ValueError(f"Unsupported input type: {type(input)}")

        return messages

    @abstractmethod
    def _turn_to_message(self, turn: Turn) -> dict:
        pass
