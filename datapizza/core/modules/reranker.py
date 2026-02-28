from abc import abstractmethod

from datapizza.core.models import PipelineComponent
from datapizza.type import Chunk


class Reranker(PipelineComponent):
    """
    A class for reranking documents.
    """

    @abstractmethod
    def rerank(self, query: str, documents: list[Chunk]) -> list[Chunk]:
        pass

    async def a_rerank(self, query: str, documents: list[Chunk]) -> list[Chunk]:
        raise NotImplementedError

    def _run(self, query: str, documents: list[Chunk]) -> list[Chunk]:
        return self.rerank(query=query, documents=documents)

    async def _a_run(self, query: str, documents: list[Chunk]) -> list[Chunk]:
        return await self.a_rerank(query=query, documents=documents)
