import inspect
from typing import Any, Literal

import jsonref
from pydantic import BaseModel


class Parameters(BaseModel):
    """Parameters of a function as defined by the OpenAI API"""

    type: Literal["object"] = "object"
    properties: dict[str, dict[str, Any]]
    required: list[str]


def type2description(k: str, v) -> str:
    # handles Annotated
    if hasattr(v, "__metadata__"):
        retval = v.__metadata__[0]
        if isinstance(retval, str):
            return retval
        else:
            raise ValueError(
                f"Invalid description {retval} for parameter {k}, should be a string."
            )
    else:
        return f"Parameter {k}"


def type2schema(t: type[Any] | None) -> Any:
    from pydantic import TypeAdapter

    schema = TypeAdapter(t).json_schema()

    json_str = jsonref.dumps(schema)
    data = jsonref.loads(json_str)

    return data


def get_parameter_json_schema(
    k: str, v: Any, default_values: dict[str, Any]
) -> dict[str, Any]:
    """Get a JSON schema for a parameter as defined by the OpenAI API

    Args:
        k: The name of the parameter
        v: The type of the parameter
        default_values: The default values of the parameters of the function

    Returns:
        A Pydanitc model for the parameter
    """

    schema = type2schema(v)
    if k in default_values:
        dv = default_values[k]
        schema["default"] = dv

    schema["description"] = type2description(k, v)

    return schema


def get_param_annotations(
    typed_signature: inspect.Signature,
) -> dict:
    """Get the type annotations of the parameters of a function

    Args:
        typed_signature: The signature of the function with type annotations

    Returns:
        A dictionary of the type annotations of the parameters of the function
    """
    return {
        k: v.annotation
        for k, v in typed_signature.parameters.items()
        if v.annotation is not inspect.Signature.empty
    }


def get_parameters(
    param_annotations: dict,
    default_values: dict,
):
    """Get the parameters of a function as defined by the OpenAI API

    Args:
        param_annotations: The type annotations of the parameters of the function
        default_values: The default values of the parameters of the function

    Returns:
        A Pydantic model for the parameters of the function
    """
    return {
        k: get_parameter_json_schema(k, v, default_values)
        for k, v in param_annotations.items()
        if v is not inspect.Signature.empty
    }


def get_required_params(typed_signature: inspect.Signature) -> list[str]:
    """Get the required parameters of a function

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A list of the required parameters of the function
    """
    return [
        k
        for k, v in typed_signature.parameters.items()
        if v.default == inspect.Signature.empty and k != "self" and k != "kwargs"
    ]


def get_default_values(typed_signature: inspect.Signature) -> dict[str, Any]:
    """Get default values of parameters of a function

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A dictionary of the default values of the parameters of the function
    """
    return {
        k: v.default
        for k, v in typed_signature.parameters.items()
        if v.default != inspect.Signature.empty
    }
