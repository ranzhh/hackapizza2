from threading import Lock

from datapizza.core.clients import Client


class ClientManager:
    _instance: Client | None = None
    _lock = Lock()

    @classmethod
    def set_global_client(cls, client: Client) -> None:
        """Set the global Client instance.

        Args:
            config: Client instance to be used globally
        """
        with cls._lock:
            cls._instance = client

    @classmethod
    def get_global_client(cls) -> Client | None:
        """Get the current global Client instance.

        Returns:
            The global client instance if set, None otherwise
        """
        return cls._instance

    @classmethod
    def clear_global_client(cls) -> None:
        """Clear the global Client instance."""
        with cls._lock:
            cls._instance = None
