from typing import Any

from ..tools import Tool, tool


def mt_test_function():
    return "test"


@tool
def decorated_function(x: int, y: str = "default") -> str:
    """Test function docstring"""
    return f"{x} {y}"


def test_tool_with_function():
    # Test basic Tool initialization with a function
    t = Tool(func=mt_test_function)
    assert t.name == "mt_test_function"
    assert "Function to mt_test_function" in t.schema["description"]
    assert t() == "test"


def test_tool_decorator_with_params():
    @tool(name="custom_name", description="Custom description", end=True)
    def mt_test_function():
        return "test"

    assert mt_test_function.name == "custom_name"
    assert mt_test_function.description == "Custom description"
    assert mt_test_function.end_invoke is True
    assert mt_test_function() == "test"


def test_tool_decorator_without_params():
    @tool
    def mt_test_function():
        """Test function docstring"""
        return "test"

    assert mt_test_function.name == "mt_test_function"
    assert isinstance(mt_test_function, Tool)
    assert mt_test_function.description == "Test function docstring"
    assert mt_test_function() == "test"
    assert mt_test_function.end_invoke is False


def test_tool_with_simple_params():
    @tool
    def mt_test_function(x: int, y: str = "default"):
        return f"{x} {y}"

    assert mt_test_function.properties == {
        "x": {"type": "integer", "description": "Parameter x"},
        "y": {"type": "string", "description": "Parameter y", "default": "default"},
    }
    assert mt_test_function.required == ["x"]


def test_tool_with_complex_params():
    @tool
    def mt_test_function(x: list[int], y: str = "default"):
        return f"{x} {y}"

    assert mt_test_function.properties == {
        "x": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Parameter x",
        },
        "y": {"type": "string", "description": "Parameter y", "default": "default"},
    }
    assert mt_test_function.required == ["x"]


def test_tool_with_any_params():
    @tool
    def mt_test_function(x: Any):
        return x

    assert mt_test_function.properties == {"x": {"description": "Parameter x"}}
    assert mt_test_function.required == ["x"]
