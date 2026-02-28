from abc import abstractmethod

from datapizza.core.clients import Client
from datapizza.core.models import PipelineComponent
from datapizza.type.type import Chunk


class Metatagger(PipelineComponent):
    """
    A meta tagger that can tag a node.
    """

    def __init__(self, client: Client):
        self.client = client

    @abstractmethod
    def tag(self, chunks: list[Chunk]):
        pass

    def a_tag(self, chunks: list[Chunk]):
        raise NotImplementedError

    def _run(self, chunks: list[Chunk]):
        return self.tag(chunks)

    async def _a_run(self, chunk: list[Chunk]):
        return await self.a_tag(chunk)
