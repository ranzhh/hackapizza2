from abc import abstractmethod

from datapizza.core.models import PipelineComponent
from datapizza.memory import Memory


class Prompt(PipelineComponent):
    """
    A class for creating prompts for RAG systems.
    """

    @abstractmethod
    def format(self, **kwargs) -> Memory:
        pass

    def _run(self, **kwargs) -> Memory:
        return self.format(**kwargs)

    async def _a_run(self, **kwargs) -> Memory:
        return self.format(**kwargs)
