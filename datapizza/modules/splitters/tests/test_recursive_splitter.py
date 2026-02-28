from datapizza.modules.splitters.recursive_splitter import RecursiveSplitter
from datapizza.type.type import Node


def test_recursive_splitter():
    recursive_splitter = RecursiveSplitter(max_char=10, overlap=0)
    chunks = recursive_splitter.split(Node(content="This is a test string"))
    assert len(chunks) == 1
