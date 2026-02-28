from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from datapizza.core.clients import Client
from datapizza.core.modules.metatagger import Metatagger
from datapizza.memory.memory import Memory
from datapizza.type import ROLE, Chunk, TextBlock


class KeywordMetatagger(Metatagger):
    """
    Keyword metatagger that uses an LLM client to add metadata to a chunk.
    """

    def __init__(
        self,
        client: Client,
        max_workers: int = 3,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        keyword_name: str = "keywords",
    ):
        """
        Args:
            client: The LLM client to use.
            max_workers: The maximum number of workers to use.
            system_prompt: The system prompt to use.
            user_prompt: The user prompt to use.
            keyword_name: The name of the keyword field.
        """
        self.client = client
        self.max_workers = max_workers
        self.keyword_name = keyword_name
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt

    def _process_chunk(self, chunk: Chunk) -> Chunk:
        # Process the text with the client and return a Chunk with metadata
        if self.user_prompt:
            memory = Memory()
            memory.add_turn(
                blocks=[
                    TextBlock(content=self.user_prompt),
                ],
                role=ROLE.USER,
            )
        else:
            memory = None

        class KeywordMetataggerOutput(BaseModel):
            keywords: list[str]

        response = self.client.structured_response(
            input=chunk.text,
            system_prompt=self.system_prompt,
            memory=memory,
            output_cls=KeywordMetataggerOutput,
        )

        updated_metadata = chunk.metadata
        updated_metadata[self.keyword_name] = response.structured_data[0].keywords  # type: ignore

        return Chunk(id=chunk.id, text=chunk.text, metadata=updated_metadata)

    def _process(self, chunks: list[Chunk]) -> list[Chunk]:
        # Process chunks concurrently
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._process_chunk, chunk) for chunk in chunks]
            results = [future.result() for future in futures]
        return results

    def __call__(self, chunks: list[Chunk]) -> list[Chunk]:
        return self._process(chunks)

    def tag(self, chunks: list[Chunk]):
        """
        Add metadata to a chunk.
        """
        return self._process(chunks)

    async def a_tag(self, chunks: list[Chunk]) -> list[Chunk]:
        """
        async Add metadata to a chunk.
        """
        raise NotImplementedError
