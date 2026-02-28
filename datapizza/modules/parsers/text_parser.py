import re

from datapizza.core.modules.parser import Parser
from datapizza.type import Node, NodeType


class TextParser(Parser):
    """
    Parser that creates a hierarchical tree structure from text.
    The hierarchy goes from document -> paragraphs -> sentences.
    """

    def __init__(self):
        """Initialize the TextParser."""
        # Regex pattern for splitting text into sentences
        self.sentence_pattern = re.compile(
            r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s"
        )

    def parse(self, text: str, metadata: dict | None = None) -> Node:
        """
        Parse text into a hierarchical tree structure.

        Args:
            text: The text to parse
            metadata: Optional metadata for the root node

        Returns:
            A Node representing the document with paragraph and sentence nodes
        """
        # Create root document node
        document_node = Node(
            children=[], metadata=metadata, node_type=NodeType.DOCUMENT
        )

        # Split text into paragraphs (based on double newlines)
        paragraphs = self._split_paragraphs(text)

        # Process each paragraph
        for i, paragraph_text in enumerate(paragraphs):
            paragraph_node = Node(
                children=[], metadata={"index": i}, node_type=NodeType.PARAGRAPH
            )

            # Split paragraph into sentences
            sentences = self._split_sentences(paragraph_text)

            # Process each sentence
            for j, sentence_text in enumerate(sentences):
                # Create sentence node with the text content in its metadata
                sentence_node = Node(
                    children=[],
                    metadata={"index": j, "text": sentence_text.strip()},
                    node_type=NodeType.SENTENCE,
                    content=sentence_text.strip(),
                )

                # Add sentence node to paragraph
                paragraph_node.add_child(sentence_node)

            # Add paragraph node to document
            document_node.add_child(paragraph_node)

        return document_node

    def a_parse(self, text: str, metadata: dict | None = None) -> Node:
        return self.parse(text, metadata)

    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs based on double newlines."""
        # Split by double newlines and filter out empty paragraphs
        paragraphs = text.strip().split("\n\n")
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_sentences(self, paragraph: str) -> list[str]:
        """Split paragraph into sentences."""
        sentences = self.sentence_pattern.split(paragraph)
        return [s.strip() for s in sentences if s.strip()]


def parse_text(text: str, metadata: dict | None = None) -> Node:
    """
    Convenience function to parse text into a hierarchical structure.

    Args:
        text: The text to parse
        metadata: Optional metadata for the root node

    Returns:
        A Node representing the document with paragraph, sentence, and word nodes
    """
    parser = TextParser()
    return parser.parse(text, metadata)
