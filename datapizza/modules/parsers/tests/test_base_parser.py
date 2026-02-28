from datapizza.modules.parsers.text_parser import TextParser
from datapizza.type import NodeType


def test_base_parser():
    parser = TextParser()
    assert parser is not None

    text = """Hello, world!


    This is a test.
    this is a sentence.
    """

    document = parser.parse(text)
    assert document is not None
    assert document.node_type == NodeType.DOCUMENT

    assert len(document.children) == 2

    assert document.children[0].node_type == NodeType.PARAGRAPH
    assert document.children[0].children[0].node_type == NodeType.SENTENCE

    assert document.children[0].content == "Hello, world!"
    assert document.children[1].content == "This is a test. this is a sentence."
    assert len(document.children[1].children) == 2
