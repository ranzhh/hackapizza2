import logging
import re
from xml.etree import ElementTree as ET

from datapizza.core.clients.client import Client
from datapizza.type import Node, NodeType

log = logging.getLogger(__name__)
# Define the system prompt
SYSTEM_PROMPT = """You are an expert text structuring tool. Your task is to analyze the given text and structure it hierarchically into sections, paragraphs, and sentences.
Output the structured text using XML-like tags: `<document>`, `<section>`, `<paragraph>`, and `<sentence>`.
Ensure all original text content is preserved within the innermost tags (sentences). Do not add any explanations or introductory text outside the main <document> tag.

Example Input:
# My Title
This is the first paragraph. It has two sentences.
This is the second paragraph.

Example Output:
<document>
<section>
<paragraph>
<sentence># My Title</sentence>
<sentence>This is the first paragraph.</sentence>
<sentence>It has two sentences.</sentence>
</paragraph>
<paragraph>
<sentence>This is the second paragraph.</sentence>
</paragraph>
</section>
</document>
"""


class LLMTreeBuilder:
    """
    TreeBuilder that creates a hierarchical tree structure from text input using an LLM.
    The hierarchy goes from document -> sections -> paragraphs -> sentences.

    params:
        client: Client - An instance of an LLM client (e.g., GeminiClient)
    """

    def __init__(
        self,
        client: Client,
        system_prompt: str | None = None,
    ):
        if not isinstance(client, Client):
            raise TypeError(
                "client must be an instance of datapizza.clients.client.Client"
            )
        self.client = client
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def parse(self, text: str) -> Node:
        """
        Build a tree from the input text using an LLM.

        Args:
            text: Input text to process

        Returns:
            A Node representing the document with hierarchical structure
        """
        if not text.strip():
            # Return an empty document node if the input text is empty
            return Node(node_type=NodeType.DOCUMENT, metadata={"source": "text_input"})

        # Call the LLM to get the structured text
        try:
            structured_text = self.client.invoke(
                input=text, system_prompt=self.system_prompt
            ).text

            if not structured_text:
                raise ValueError("LLM returned an empty structure.")

        except Exception as e:
            # Fallback or error handling: Treat the whole text as a single paragraph/sentence document
            log.error(
                f"Error calling LLM or parsing response: {e}. Falling back to basic structure."
            )
            sentence_node = Node(node_type=NodeType.SENTENCE, content=text.strip())
            paragraph_node = Node(
                node_type=NodeType.PARAGRAPH, children=[sentence_node]
            )
            section_node = Node(node_type=NodeType.SECTION, children=[paragraph_node])
            return Node(
                node_type=NodeType.DOCUMENT,
                children=[section_node],
                metadata={"source": "text_input", "llm_fallback": True},
            )

        # Parse the XML-like structure from the LLM response
        try:
            # Attempt to clean potential LLM artifacts before parsing
            clean_xml = self._clean_llm_output(structured_text)
            root_element = ET.fromstring(clean_xml)

            document_node = self._parse_element(root_element)
            # Add source metadata to the root document node
            if not document_node.metadata:
                document_node.metadata = {}
            document_node.metadata["source"] = "text_input"
            document_node.metadata["llm_structured"] = True

            return document_node
        except ET.ParseError as e:
            # Handle cases where the LLM output is not valid XML even after cleaning
            log.error(
                f"XML parsing failed after cleaning: {e}. Falling back to basic structure."
            )
            # Fallback: Treat the whole original text as a single paragraph/sentence document
            sentence_node = Node(node_type=NodeType.SENTENCE, content=text.strip())
            paragraph_node = Node(
                node_type=NodeType.PARAGRAPH, children=[sentence_node]
            )
            section_node = Node(node_type=NodeType.SECTION, children=[paragraph_node])
            return Node(
                node_type=NodeType.DOCUMENT,
                children=[section_node],
                metadata={
                    "source": "text_input",
                    "llm_fallback": True,
                    "parse_error": str(e),
                },
            )
        except Exception as e:  # Catch other potential errors during node creation
            log.error(
                f"Error processing LLM structure: {e}. Falling back to basic structure."
            )
            # Fallback: Treat the whole original text as a single paragraph/sentence document
            sentence_node = Node(node_type=NodeType.SENTENCE, content=text.strip())
            paragraph_node = Node(
                node_type=NodeType.PARAGRAPH, children=[sentence_node]
            )
            section_node = Node(node_type=NodeType.SECTION, children=[paragraph_node])
            return Node(
                node_type=NodeType.DOCUMENT,
                children=[section_node],
                metadata={
                    "source": "text_input",
                    "llm_fallback": True,
                    "processing_error": str(e),
                },
            )

    def _parse_element(self, element: ET.Element) -> Node:
        """Recursively parse an XML element into a Node."""
        tag_map = {
            "document": NodeType.DOCUMENT,
            "section": NodeType.SECTION,
            "paragraph": NodeType.PARAGRAPH,
            "sentence": NodeType.SENTENCE,
        }
        node_type = tag_map.get(element.tag.lower())

        if node_type is None:
            # If the tag is unrecognized, treat it as a sentence containing its text
            # This handles potential unexpected tags from the LLM
            print(f"Warning: Unrecognized tag '{element.tag}'. Treating as sentence.")
            content = (element.text or "").strip()
            for child in (
                element
            ):  # Also capture text from child elements if any unexpected nesting occurs
                content += (child.text or "").strip() + (child.tail or "").strip()
            return Node(node_type=NodeType.SENTENCE, content=content)

        if node_type == NodeType.SENTENCE:
            # Leaf node: extract text content
            content = (element.text or "").strip()
            # Also capture text after potential nested tags if any (shouldn't happen with prompt)
            for child in element:
                content += (child.tail or "").strip()
            return Node(node_type=node_type, content=content)
        else:
            # Internal node: recursively parse children
            # Only process child elements, ignore text directly within this node or tails
            children = [
                self._parse_element(child) for child in element if child.tag in tag_map
            ]  # Filter out unrecognized tags if necessary
            # Filter out any None results if _parse_element potentially returns None (though current logic shouldn't)
            children = [child for child in children if child is not None]
            return Node(node_type=node_type, children=children)

    def _clean_llm_output(self, xml_string: str) -> str:
        """Basic cleaning of LLM output to improve XML parsing robustness."""
        # Remove potential markdown code blocks ```xml ... ```
        xml_string = re.sub(
            r"```xml\\s*(.*?)\\s*```",
            r"\\1",
            xml_string,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove potential leading/trailing whitespace or explanations outside the root tag
        match = re.search(
            r"<document>.*</document>", xml_string, re.DOTALL | re.IGNORECASE
        )
        cleaned_xml = (
            match.group(0).strip() if match else xml_string.strip()
        )  # Fallback if no document tag found

        # Escape special XML characters in the text content
        # Use a function to avoid replacing entities that might already be correct
        def escape_entities(text):
            text = text.replace("&", "&amp;")  # Must be first
            text = text.replace("<", "&lt;")
            text = text.replace(">", "&gt;")
            return text

        # Apply escaping only to text content, not tags
        # This is a simplified approach; a proper XML parser would handle this better,
        # but ET.fromstring needs valid input first.
        # We split by tags and escape content in between.
        parts = re.split(r"(<[^>]+>)", cleaned_xml)
        escaped_parts = []
        for i, part in enumerate(parts):
            if i % 2 == 0:  # Text content (even indices)
                escaped_parts.append(escape_entities(part))
            else:  # Tag (odd indices)
                escaped_parts.append(part)

        return "".join(escaped_parts)

    def invoke(self, file_path: str) -> Node:
        """
        Invoke the tree builder on the input text using an LLM.

        Args:
            file_path: Path to the file to process

        Returns:
            A Node representing the document with hierarchical structure
        """
        with open(file_path) as file:
            text = file.read()
        return self.parse(text)

    def __call__(self, file_path: str) -> Node:
        return self.invoke(file_path)

    def _run(self, file_path: str) -> Node:
        return self.invoke(file_path)

    async def _a_run(self, file_path: str) -> Node:
        raise NotImplementedError("LLMTreeBuilder does not support async operations")
