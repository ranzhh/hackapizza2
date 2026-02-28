import hashlib
import json

from datapizza.type import ROLE, Block, FunctionCallBlock


class Turn:
    def __init__(
        self,
        blocks: list[Block],
        role: ROLE = ROLE.ASSISTANT,
    ):
        if not isinstance(blocks, list):
            raise ValueError("blocks must be a list")
        if not all(isinstance(block, Block) for block in blocks):
            raise ValueError("all items in blocks must be Block instances")

        self.blocks = blocks
        self.role = role

    def __iter__(self):
        return iter(self.blocks)

    def __len__(self):
        return len(self.blocks)

    def __getitem__(self, index):
        return self.blocks[index]

    def __setitem__(self, index, value):
        self.blocks[index] = value

    def __delitem__(self, index):
        del self.blocks[index]

    def append(self, block: Block):
        self.blocks.append(block)

    def extend(self, blocks: list[Block]):
        self.blocks.extend(blocks)

    def insert(self, index, block: Block):
        self.blocks.insert(index, block)

    def to_dict(self):
        return {
            "role": self.role.value,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    def __str__(self):
        return str(self.blocks)

    def __repr__(self):
        return f"Turn(blocks={self.blocks}, role={self.role})"


class Memory:
    """
    A class for storing the memory of a chat, organized by conversation turns.
    Each turn can contain multiple blocks (text, function calls, or structured data).
    """

    def __init__(self):
        # List of turns, where each turn is a list of blocks
        self.memory = []

    def new_turn(self, role: ROLE = ROLE.ASSISTANT):
        """Add a new conversation turn.

        Args:
            role (ROLE, optional): The role of the new turn. Defaults to ROLE.ASSISTANT.
        """
        self.memory.append(Turn([], role))

    def add_turn(
        self, blocks: list[Block] | list[FunctionCallBlock] | Block, role: ROLE
    ):
        """Add a new conversation turn containing one or more blocks.

        Args:
            blocks (list[Block] | Block): The blocks to add to the new turn.
            role (ROLE): The role of the new turn.
        """

        turn = Turn(blocks, role) if isinstance(blocks, list) else Turn([blocks], role)  # type: ignore

        self.memory.append(turn)

    def add_to_last_turn(self, block: Block):
        """Add a block to the most recent turn. Creates a new turn if memory is empty.
        Args:
            block (Block): The block to add to the most recent turn.
        """
        if not self.memory:
            self.memory.append(Turn([block], ROLE.ASSISTANT))
        else:
            self.memory[-1].append(block)

    def clear(self):
        """Clear all memory."""
        self.memory = []

    def __iter__(self):
        """Iterate through all blocks in all turns."""
        yield from self.memory

    def iter_blocks(self):
        """
        Iterate through blocks.
        """
        for turn in self.memory:
            yield from turn

    def copy(self):
        """Deep copy the memory."""
        from copy import deepcopy

        memory = Memory()
        memory.memory = deepcopy(self.memory)
        return memory

    def __len__(self):
        """Return the total number of turns."""
        return len(self.memory)

    def __getitem__(self, index):
        """Get all blocks from a specific turn."""
        return self.memory[index]

    def __setitem__(self, index, value):
        """Set blocks for a specific turn."""
        if isinstance(value, list):
            self.memory[index] = value
        else:
            self.memory[index] = [value]

    def __delitem__(self, index):
        """Delete a specific turn."""
        del self.memory[index]

    def __str__(self):
        """Return a string representation of the memory."""
        return str(self.memory)

    def __repr__(self):
        """Return a detailed string representation of the memory."""
        return f"Memory(turns={len(self)})"

    def __bool__(self):
        """Return True if memory contains any turns, False otherwise."""
        return bool(self.memory)

    def __eq__(self, other):
        """
        Compare two Memory objects based on their content hash.
        This is more efficient than comparing the full content structure.
        """
        if not isinstance(other, Memory):
            return False
        return hash(self) == hash(other)

    def __hash__(self):
        """
        Creates a deterministic hash based on the content of memory turns.
        """
        hash_components = []
        for turn in self.memory:
            turn_components = []
            for block in turn.blocks:
                turn_components.append(str(hash(block)))
            hash_components.append("|".join(turn_components))

        content_string = "||".join(hash_components)
        return int(hashlib.sha256(content_string.encode("utf-8")).hexdigest(), 16)

    def json_dumps(self) -> str:
        """Serialize the memory to JSON.

        Returns:
            str: The JSON representation of the memory.
        """
        return json.dumps(self.to_dict())

    def json_loads(self, json_str: str):
        """Deserialize JSON to the memory.

        Args:
            json_str (str): The JSON string to deserialize.
        """
        obj = json.loads(json_str)
        for t in obj:
            self.add_turn(
                blocks=[Block.from_dict(block) for block in t["blocks"]],
                role=ROLE(t["role"]),
            )

    def to_dict(self) -> list[dict]:
        """Convert memory to a dictionary.

        Returns:
            list[dict]: The dictionary representation of the memory.
        """
        return [turn.to_dict() for turn in self.memory]
