from abc import ABC, abstractmethod
from typing import Any

from datapizza.tracing.tracing import tracer


class ChainableProducer(ABC):
    """
    A class that can produce a module.
    If a ChainableProducer is used as a node in a pipeline, it will produce a module.
    """

    def as_module_component(self):
        """
        Returns a module component that can be used in a pipeline.
        """
        return self._as_module_component()

    @abstractmethod
    def _as_module_component(self):
        raise NotImplementedError("Subclasses must implement _as_module_component")


class PipelineComponent(ABC):
    """
    Abstract base class for components that can be used in datapizza-ai pipelines.

    Provides a common __call__ interface for execution logging and delegates
    the core logic to the component's _run/_a_run methods.

    Supports both synchronous and asynchronous processing through _run and
    _a_run methods respectively.
    """

    def __call__(self, *args, **kwargs):
        """
        Synchronous execution entry point that delegates to run.

        This method is called when the component is called in a synchronous context.
        """
        return self.run(*args, **kwargs)

    def validate_input(self, args, kwargs):
        """
        Validate the input of the component.
        """
        assert 1 == 1

    def validate_output(self, data):
        """
        Validate the output of the component.
        """
        assert 1 == 1

    def run(self, *args, **kwargs) -> Any:
        """
        Synchronous execution wrapper around _run with tracing and validation.

        This method is called when the component is executed in a sync context.
        """
        with tracer.start_as_current_span(
            f"PipelineComponent.{self.__class__.__name__}"
        ):
            self.validate_input(args, kwargs)
            data = self._run(*args, **kwargs)
            self.validate_output(data)
            return data

    async def a_run(self, *args, **kwargs) -> Any:
        """
        Asynchronous execution wrapper around _a_run with tracing and validation.

        This method is called when the component is awaited in an async context.
        """
        with tracer.start_as_current_span(
            f"PipelineComponent.{self.__class__.__name__}"
        ):
            self.validate_input(args, kwargs)
            data = await self._a_run(*args, **kwargs)
            self.validate_output(data)
            return data

    @abstractmethod
    def _run(self, *args, **kwargs) -> Any:
        """
        The core processing logic of the component.

        Subclasses must implement this method to define their specific behavior.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement the _run method"
        )

    async def _a_run(self, *args, **kwargs) -> Any:
        """
        The asynchronous core processing logic of the component.

        Subclasses must implement this method to define their async-specific behavior.
        For simple cases, this can delegate to the synchronous _run method.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement the _a_run method"
        )
