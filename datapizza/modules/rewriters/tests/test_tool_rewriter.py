from datapizza.clients import MockClient
from datapizza.modules.rewriters import ToolRewriter


def test_tool_rewriter():
    tool_rewriter = ToolRewriter(
        client=MockClient(model_name="test"),
        system_prompt="test",
    )
    assert tool_rewriter is not None
