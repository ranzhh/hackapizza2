import asyncio
from concurrent.futures import Future, ThreadPoolExecutor

from datapizza.core.clients import Client
from datapizza.core.modules.captioner import NodeCaptioner
from datapizza.type import Media, MediaBlock, MediaNode, Node, NodeType


class LLMCaptioner(NodeCaptioner):
    """
    Captioner that uses an LLM client to caption a node.
    """

    def __init__(
        self,
        client: Client,
        max_workers: int = 3,
        system_prompt_table: str | None = "Generate concise captions for tables.",
        system_prompt_figure: str | None = "Generate descriptive captions for figures.",
    ):
        """
        Captioner that uses an LLM client to caption a node.
        Args:
            client: The LLM client to use.
            max_workers: The maximum number of workers to use. in sync mode is the number of threads spawned, in async mode is the number of batches.
            system_prompt_table: The system prompt to use for table captioning.
            system_prompt_figure: The system prompt to use for figure captioning.
        """
        self.client = client
        self.max_workers = max_workers
        self.system_prompt_figure = system_prompt_figure
        self.system_prompt_table = system_prompt_table

    def _get_all_media_nodes(self, node: Node) -> list[MediaNode]:
        if isinstance(node, MediaNode):
            return [node]

        media_nodes = []
        for child in node.children:
            media_nodes.extend(self._get_all_media_nodes(child))
        return media_nodes

    def _process_media(self, media_node: MediaNode) -> tuple[MediaNode, str]:
        if media_node.node_type == NodeType.FIGURE:
            system_prompt = self.system_prompt_figure
        elif media_node.node_type == NodeType.TABLE:
            system_prompt = self.system_prompt_table
        else:
            raise ValueError(f"Unsupported node type: {media_node.node_type}")

        caption = self.caption_media(
            media=media_node.media, system_prompt=system_prompt
        )

        # Create a new MediaNode with the caption nodes
        new_children = [
            Node(
                node_type=NodeType.PARAGRAPH,
                content=f" {media_node.content} <{media_node.node_type.value}> [{caption}]",
                metadata=media_node.metadata,
            ),
            *media_node.children,
            Node(
                node_type=NodeType.PARAGRAPH,
                content=f"</{media_node.node_type.value}>",
                metadata=media_node.metadata,
            ),
        ]

        new_media_node = MediaNode(
            node_type=media_node.node_type,
            media=media_node.media,
            children=new_children,
            metadata=media_node.metadata,
        )

        return new_media_node, caption

    def _replace_media_nodes(
        self, node: Node, processed_nodes: list[MediaNode]
    ) -> Node:
        if isinstance(node, MediaNode):
            # Find matching processed node
            for processed_node in processed_nodes:
                if processed_node.media == node.media:
                    return processed_node
            return node

        # Create new node with processed children
        new_children = [
            self._replace_media_nodes(child, processed_nodes) for child in node.children
        ]
        return Node(
            node_type=node.node_type,
            content=node.content,
            children=new_children,
            metadata=node.metadata,
        )

    def caption(self, node: Node) -> Node:
        """
        Caption a node.
        Args:
            node: The node to caption.

        Returns:
            The same node with the caption.
        """
        media_nodes = self._get_all_media_nodes(node)

        # Process media nodes concurrently
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: list[Future] = [
                executor.submit(self._process_media, media) for media in media_nodes
            ]

            # Wait for all futures to complete and collect results
            processed_nodes = []
            for future in futures:
                processed_node, _ = future.result()
                processed_nodes.append(processed_node)

        return self._replace_media_nodes(node, processed_nodes)

    def caption_media(self, media: Media, system_prompt: str | None = None) -> str:
        """
        Caption an image.
        Args:
            media: The media to caption.
            system_prompt: Optional system prompt to guide the captioning.

        Returns:
            The string caption.
        """
        response = self.client.invoke(
            input=[MediaBlock(media)], system_prompt=system_prompt
        )
        return response.text

    async def a_caption(self, node: Node) -> Node:
        """
        async Caption a node.
        Args:
            node: The node to caption.

        Returns:
            The same node with the caption.
        """
        media_nodes = self._get_all_media_nodes(node)

        # Process media nodes in batches based on max_workers
        processed_nodes = []
        for i in range(0, len(media_nodes), self.max_workers):
            batch = media_nodes[i : i + self.max_workers]

            # Process batch concurrently
            results = await asyncio.gather(
                *[self._a_process_media(media) for media in batch]
            )

            # Unpack results from this batch
            processed_nodes.extend([node for node, _ in results])

        return self._replace_media_nodes(node, processed_nodes)

    async def a_caption_media(
        self, media: Media, system_prompt: str | None = None
    ) -> str:
        """
        async Caption image.
        Args:
            media: The media to caption.
            system_prompt: Optional system prompt to guide the captioning.

        Returns:
            The string caption.
        """
        response = await self.client.a_invoke(
            input=[MediaBlock(media)], system_prompt=system_prompt
        )
        return response.text

    async def _a_process_media(self, media_node: MediaNode) -> tuple[MediaNode, str]:
        if media_node.node_type == NodeType.FIGURE:
            system_prompt = self.system_prompt_figure
        elif media_node.node_type == NodeType.TABLE:
            system_prompt = self.system_prompt_table
        else:
            raise ValueError(f"Unsupported node type: {media_node.node_type}")

        caption = await self.a_caption_media(
            media=media_node.media, system_prompt=system_prompt
        )

        # Create a new MediaNode with the caption nodes
        new_children = [
            Node(
                node_type=NodeType.PARAGRAPH,
                content=f" {media_node.content} <{media_node.node_type.value}> [{caption}]",
                metadata=media_node.metadata,
            ),
            *media_node.children,
            Node(
                node_type=NodeType.PARAGRAPH,
                content=f"</{media_node.node_type.value}>",
                metadata=media_node.metadata,
            ),
        ]

        new_media_node = MediaNode(
            node_type=media_node.node_type,
            media=media_node.media,
            children=new_children,
            metadata=media_node.metadata,
        )

        return new_media_node, caption
