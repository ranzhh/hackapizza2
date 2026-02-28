import copy
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import yaml
from opentelemetry import trace

from datapizza.core.models import PipelineComponent
from datapizza.core.utils import replace_env_vars

tracer = trace.get_tracer(__name__)
log = logging.getLogger(__name__)


@dataclass
class Dependency:
    """Dependency for a node."""

    node_name: str
    input_key: str | None = None
    target_key: str | None = None


class FunctionalPipeline:
    """Pipeline for executing a series of nodes with dependencies."""

    def __init__(self):
        self.nodes = []

    def run(
        self,
        name: str,
        node: PipelineComponent,
        dependencies: list[Dependency] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> "FunctionalPipeline":
        """
        Add a node to the pipeline with optional dependencies.

        Args:
            name (str): The name of the node.
            node (PipelineComponent): The node to add.
            dependencies (list[Dependency], optional): List of dependencies for the node. Defaults to None.
            kwargs (dict[str, Any], optional): Additional keyword arguments to pass to the node. Defaults to None.

        Returns:
            FunctionalPipeline: The pipeline instance.
        """
        if dependencies is None:
            dependencies = []
        if kwargs is None:
            kwargs = {}

        self.nodes.append(
            {
                "name": name,
                "node": node,
                "dependencies": dependencies,
                "type": "node",
                "kwargs": kwargs,
            }
        )
        return self

    def then(
        self,
        name: str,
        node: PipelineComponent,
        target_key: str,
        dependencies: list[Dependency] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> "FunctionalPipeline":
        """
        Add a node to execute after the previous node.

        Args:
            name (str): The name of the node.
            node (PipelineComponent): The node to add.
            target_key (str): The key to store the result of the node in the previous node.
            dependencies (list[Dependency], optional): List of dependencies for the node. Defaults to None.
            kwargs (dict[str, Any], optional): Additional keyword arguments to pass to the node. Defaults to None.

        Returns:
            FunctionalPipeline: The pipeline instance.
        """
        deps = []
        prev_node = self.nodes[-1]
        deps = [Dependency(node_name=prev_node["name"], target_key=target_key)]
        if dependencies:
            deps.extend(dependencies)

        return self.run(name, node, deps, kwargs)

    def foreach(
        self,
        name: str,
        do: "PipelineComponent | FunctionalPipeline",
        dependencies: list[Dependency] | None = None,
    ) -> "FunctionalPipeline":
        """
        Execute a sub-pipeline for each item in a collection.

        Args:
            name (str): The name of the node.
            do (PipelineComponent): The sub-pipeline to execute for each item.
            dependencies (list[Dependency], optional): List of dependencies for the node. Defaults to None.

        Returns:
            FunctionalPipeline: The pipeline instance.

        """
        if dependencies is None:
            dependencies = []

        if isinstance(dependencies, Dependency):
            dependencies = [dependencies]

        if not isinstance(do, PipelineComponent):
            raise TypeError("do must be a PipelineComponent")

        self.nodes.append(
            {
                "name": name,
                "do": do,
                "dependencies": dependencies,
                "type": "foreach",
            }
        )
        return self

    def branch(
        self,
        condition: Callable,
        if_true: "FunctionalPipeline",
        if_false: "FunctionalPipeline",
        dependencies: list[Dependency] | None = None,
    ) -> "FunctionalPipeline":
        """
        Branch execution based on a condition.

        Args:
            condition (Callable): The condition to evaluate.
            if_true (FunctionalPipeline): The pipeline to execute if the condition is True.
            if_false (FunctionalPipeline): The pipeline to execute if the condition is False.
            dependencies (list[Dependency], optional): List of dependencies for the node. Defaults to None.

        Returns:
            FunctionalPipeline: The pipeline instance.
        """
        if dependencies is None:
            dependencies = []

        self.nodes.append(
            {
                "condition": condition,
                "if_true": if_true,
                "if_false": if_false,
                "dependencies": dependencies,
                "type": "branch",
            }
        )
        return self

    def get(self, name: str) -> "FunctionalPipeline":
        """
        Get the result of a node.

        Args:
            name (str): The name of the node.

        Returns:
            FunctionalPipeline: The pipeline instance.
        """
        self.nodes.append({"get_name": name, "type": "get"})
        return self

    def _resolve_dependencies(self, node, context):
        """Resolve dependencies for a node."""
        inputs = {}
        dependencies = node.get("dependencies", [])
        for dep in dependencies:
            if dep.node_name in context:
                if dep.target_key:
                    inputs[dep.target_key] = context[dep.node_name]
                else:
                    if len(dependencies) > 1:
                        raise ValueError(
                            f"Target key is required for node {dep.node_name} because it has more than 1 dependencies"
                        )

                    return context[dep.node_name]

        return inputs

    @tracer.start_as_current_span("functional_pipeline.execute")
    def execute(
        self,
        initial_data: dict[str, Any] | None = None,
        context: dict | None = None,  # type: ignore
    ) -> dict[str, Any]:
        """Execute the pipeline and return the results.

        Args:
            initial_data: Dictionary where keys are node names and values are the data
                         to be passed to those nodes when they execute.
            context: Dictionary where keys are node names and values are the data
                     to be passed to those nodes when they execute.

        Returns:
            dict: The results of the pipeline.
        """
        context: dict = context or {}
        initial_data = initial_data or {}

        for node in self.nodes:
            node_type = node.get("type")

            if node_type == "node":
                # Get dependencies from context
                dep_inputs = self._resolve_dependencies(node, context)

                # Get initial data for this node if available
                node_input = initial_data.get(node["name"], {})

                # Merge dependency inputs with node-specific initial data
                # Node-specific initial data takes precedence
                inputs = (
                    {**dep_inputs, **node_input, **node["kwargs"]}
                    if isinstance(node_input, dict)
                    else node_input
                )

                # Execute the node
                result = node["node"].run(**inputs)

                # Store the result in context
                context[node["name"]] = result

            elif node_type == "foreach":
                node_name = node.get("name")
                to_do = node.get("do")

                # Get dependencies data
                dep_inputs = self._resolve_dependencies(
                    node, {**context, **initial_data}
                )

                # Get collection to iterate over
                collection = dep_inputs  # .get(node_name) or []

                if not isinstance(collection, list):
                    collection = [collection]

                results = []
                for item in collection:
                    log.debug(f"Executing {node_name} for item: {item}")
                    # Execute sub-pipeline with the prepared initial data

                    if isinstance(to_do, PipelineComponent):
                        item_result = to_do.run(item)

                    # item_result = node["do"].execute(
                    #     initial_data=initial_data,
                    #     context={**{node_name: item}},
                    # )
                    if isinstance(item_result, list):
                        results.extend(item_result)
                    else:
                        results.append(item_result)

                context[node["name"]] = results

            elif node_type == "branch":
                # Get dependencies data
                dep_inputs = self._resolve_dependencies(node, context)

                # Evaluate condition with context
                condition_result = node["condition"](context)

                # Pass initial data to the chosen branch
                branch_initial_data = initial_data.get("branch", context)

                if condition_result:
                    branch_result = node["if_true"].execute(branch_initial_data)
                else:
                    branch_result = node["if_false"].execute(branch_initial_data)

                # Merge branch results into context
                context.update(branch_result)

            elif node_type == "parallel":
                # Get dependencies data
                dep_inputs = self._resolve_dependencies(node, context)

                # Get initial data for parallel execution
                parallel_initial_data = initial_data.get("parallel", {})

                parallel_results = []
                for i, pipeline in enumerate(node["pipelines"]):
                    # Get pipeline-specific initial data
                    pipeline_initial_data = parallel_initial_data.get(str(i), {})

                    # Create a copy of the context for each parallel branch
                    branch_context = copy.deepcopy(pipeline_initial_data)
                    result = pipeline.execute(branch_context)
                    parallel_results.append(result)

                # Merge all results from parallel execution
                for i, result in enumerate(parallel_results):
                    key = f"parallel_{i}"
                    context[key] = result

            elif node_type == "get":
                context = context[node["get_name"]]

        return context

    @staticmethod
    def from_yaml(yaml_path: str) -> "FunctionalPipeline":
        """
        Constructs a FunctionalPipeline from a YAML configuration file.
        The YAML should contain 'modules' (optional) defining reusable components
        and 'pipeline' defining the sequence of steps.

        Args:
            yaml_path: Path to the YAML configuration file.

        Returns:
            A configured FunctionalPipeline instance.

        Raises:
            ValueError: If the YAML format is invalid, a module cannot be loaded,
                        or a referenced node/condition name is not found.
            KeyError: If a required key is missing in the YAML structure.
            FileNotFoundError: If the yaml_path does not exist.
            yaml.YAMLError: If the YAML file cannot be parsed.
            ImportError: If a specified module cannot be imported.
            AttributeError: If a specified class/function is not found in the module.
        """
        try:
            with open(yaml_path) as f:
                raw_config = yaml.safe_load(f)
        except FileNotFoundError:
            log.error(f"YAML file not found at path: {yaml_path}")
            raise
        except yaml.YAMLError as e:
            log.error(f"Error parsing YAML file {yaml_path}: {e}")
            raise ValueError(f"Invalid YAML format: {e}") from e

        # Process the entire config to replace environment variables
        config = replace_env_vars(raw_config)

        if not isinstance(config, dict):
            raise ValueError("YAML config must be a dictionary.")

        # --- Load Modules (Nodes and Callables) ---
        loaded_nodes: dict[str, PipelineComponent] = {}
        if "modules" in config:
            if not isinstance(config["modules"], list):
                raise ValueError("YAML 'modules' section must be a list.")

            for i, module_config in enumerate(config["modules"]):
                if not isinstance(module_config, dict):
                    raise ValueError(
                        f"Module definition {i + 1} is not a dictionary: {module_config}"
                    )
                try:
                    module_name = module_config["name"]
                    module_path = module_config["module"]
                    class_or_func_name = module_config["type"]
                    params = module_config.get("params", {})

                    imported_module = importlib.import_module(module_path)
                    class_or_func: PipelineComponent = getattr(
                        imported_module, class_or_func_name
                    )

                    # Process params - resolve node references
                    processed_params = FunctionalPipeline._process_params(
                        params, loaded_nodes
                    )

                    # Instantiate if it's a class, otherwise use the function directly
                    if isinstance(class_or_func, type):  # Check if it's a class type
                        # Ensure parameters are passed correctly
                        instance = class_or_func(**processed_params)
                        loaded_nodes[module_name] = instance
                    elif callable(class_or_func):  # Check if it's a callable (function)
                        # Functions typically don't take init params like classes
                        if processed_params:
                            log.warning(
                                f"Params provided for function '{class_or_func_name}' (module '{module_name}') but functions usually don't take init params. Ignoring params: {processed_params}"
                            )
                        loaded_nodes[module_name] = class_or_func
                    else:
                        raise TypeError(
                            f"Loaded object '{class_or_func_name}' from module '{module_path}' is neither a class nor a callable function."
                        )

                except KeyError as e:
                    raise ValueError(
                        f"Missing required key {e} in module definition {i + 1}: {module_config}"
                    ) from e
                except (ImportError, AttributeError) as e:
                    raise ValueError(
                        f"Could not load type '{module_config.get('type', 'N/A')}' from module '{module_config.get('module', 'N/A')}' for component '{module_config.get('name', 'N/A')}': {e}"
                    ) from e
                except Exception as e:
                    log.error(
                        f"Failed to load or instantiate module '{module_config.get('name', 'N/A')}': {e}"
                    )
                    raise  # Re-raise the exception

        # --- Build Pipeline ---
        if "pipeline" not in config:
            raise ValueError(
                "YAML config must have a top-level 'pipeline' key containing a list of steps."
            )

        pipeline_config = config["pipeline"]
        if not isinstance(pipeline_config, list):
            raise ValueError("The 'pipeline' key must contain a list of steps.")

        # Call the helper method to build the pipeline using loaded nodes
        try:
            pipeline = FunctionalPipeline._build_pipeline_from_config(
                pipeline_config, loaded_nodes
            )
            return pipeline
        except (ValueError, KeyError, TypeError) as e:
            log.error(f"Error building pipeline from YAML config: {e}")
            raise

    @staticmethod
    def _process_params(params: dict, loaded_nodes: dict) -> dict:
        """
        Process parameters to resolve node references.

        Args:
            params: The parameters to process
            loaded_nodes: Dictionary of loaded node instances

        Returns:
            Processed parameters with node references resolved
        """
        if not params:
            return {}

        processed_params = {}

        for key, value in params.items():
            # Check if this is a string with curly braces format {node_name}
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                node_name = value[1:-1]  # Remove the curly braces
                if node_name in loaded_nodes:
                    processed_params[key] = loaded_nodes[node_name]
                else:
                    raise ValueError(
                        f"Node reference '{node_name}' not found. Available nodes: {list(loaded_nodes.keys())}"
                    )
            # If it's a nested dictionary
            elif isinstance(value, dict):
                # First check for direct node references in the dictionary values
                processed_dict = {}
                for dict_key, dict_value in value.items():
                    if (
                        isinstance(dict_value, str)
                        and dict_value.startswith("{")
                        and dict_value.endswith("}")
                    ):
                        node_name = dict_value[1:-1]  # Remove the curly braces
                        if node_name in loaded_nodes:
                            processed_dict[dict_key] = loaded_nodes[node_name]
                        else:
                            raise ValueError(
                                f"Node reference '{node_name}' not found. Available nodes: {list(loaded_nodes.keys())}"
                            )
                    else:
                        processed_dict[dict_key] = dict_value

                # Then recursively process any remaining complex values
                processed_params[key] = FunctionalPipeline._process_params(
                    processed_dict, loaded_nodes
                )
            # If it's a list, check each item for node references
            elif isinstance(value, list):
                processed_list = []
                for item in value:
                    if (
                        isinstance(item, str)
                        and item.startswith("{")
                        and item.endswith("}")
                    ):
                        node_name = item[1:-1]  # Remove the curly braces
                        if node_name in loaded_nodes:
                            processed_list.append(loaded_nodes[node_name])
                        else:
                            raise ValueError(
                                f"Node reference '{node_name}' not found in list. Available nodes: {list(loaded_nodes.keys())}"
                            )
                    elif isinstance(item, dict):
                        processed_list.append(
                            FunctionalPipeline._process_params(item, loaded_nodes)
                        )
                    else:
                        processed_list.append(item)
                processed_params[key] = processed_list
            else:
                processed_params[key] = value

        return processed_params

    @staticmethod
    def _build_pipeline_from_config(
        pipeline_config: list[dict[str, Any]],
        nodes_map: dict[str, PipelineComponent],
    ) -> "FunctionalPipeline":
        """Helper method to recursively build a pipeline from a config list."""
        pipeline = FunctionalPipeline()

        for i, step in enumerate(pipeline_config):
            if not isinstance(step, dict):
                raise ValueError(
                    f"Step {i + 1} in pipeline config is not a dictionary: {step}"
                )

            step_type = step.get("type")
            if not step_type:
                raise ValueError(f"Step {i + 1} missing 'type': {step}")

            name = step.get("name")
            node_ref = step.get("node")
            dependencies_config = step.get("dependencies", [])
            kwargs = step.get("kwargs", {})
            target_key = step.get("target_key")
            get_name = step.get("get_name")

            # Validate and build dependencies
            if not isinstance(dependencies_config, list):
                raise ValueError(
                    f"Step {i + 1} ('{step_type}'): 'dependencies' must be a list, got: {dependencies_config}"
                )
            try:
                dependencies = [Dependency(**dep) for dep in dependencies_config]
            except TypeError as e:
                raise ValueError(
                    f"Step {i + 1} ('{step_type}'): Invalid format in 'dependencies': {e}. Each dependency must be a dict with keys matching Dependency fields (node_name, input_key, target_key). Config: {dependencies_config}"
                ) from e

            # Resolve node from map if specified
            node_instance: PipelineComponent
            if node_ref:
                if not isinstance(node_ref, str):
                    raise ValueError(
                        f"Step {i + 1} ('{step_type}'): 'node' reference must be a string name, got: {node_ref}"
                    )
                if node_ref not in nodes_map:
                    raise KeyError(
                        f"Step {i + 1} ('{step_type}'): Node '{node_ref}' not found in provided nodes_map. Available: {list(nodes_map.keys())}"
                    )
                node_instance = nodes_map[node_ref]

            # Process kwargs to resolve any node references
            processed_kwargs = FunctionalPipeline._process_params(kwargs, nodes_map)

            # --- Handle different step types ---
            if step_type == "run":
                if not name or not node_instance:
                    raise ValueError(
                        f"Step {i + 1}: 'run' step requires 'name' (string) and resolved 'node', got name={name}, node_ref={node_ref}. Step: {step}"
                    )
                if not isinstance(name, str):
                    raise ValueError(
                        f"Step {i + 1}: 'run' step 'name' must be a string, got {type(name)}. Step: {step}"
                    )
                pipeline.run(
                    name=name,
                    node=node_instance,
                    dependencies=dependencies,
                    kwargs=processed_kwargs,
                )

            elif step_type == "then":
                if not name or not node_instance or not target_key:
                    raise ValueError(
                        f"Step {i + 1}: 'then' step requires 'name' (string), resolved 'node', and 'target_key' (string), got name={name}, node_ref={node_ref}, target_key={target_key}. Step: {step}"
                    )
                if not isinstance(name, str) or not isinstance(target_key, str):
                    raise ValueError(
                        f"Step {i + 1}: 'then' step 'name' and 'target_key' must be strings, got name type {type(name)}, target_key type {type(target_key)}. Step: {step}"
                    )
                pipeline.then(
                    name=name,
                    node=node_instance,
                    target_key=target_key,
                    dependencies=dependencies,
                    kwargs=processed_kwargs,
                )

            elif step_type == "get":
                if not get_name:
                    raise ValueError(
                        f"Step {i + 1}: 'get' step requires 'get_name' (string), got {get_name}. Step: {step}"
                    )
                if not isinstance(get_name, str):
                    raise ValueError(
                        f"Step {i + 1}: 'get' step 'get_name' must be a string, got {type(get_name)}. Step: {step}"
                    )
                pipeline.get(name=get_name)

            elif step_type == "foreach":
                if not name:
                    raise ValueError(
                        f"Step {i + 1}: 'foreach' step requires 'name' (string), got {name}. Step: {step}"
                    )
                if not isinstance(name, str):
                    raise ValueError(
                        f"Step {i + 1}: 'foreach' step 'name' must be a string, got {type(name)}. Step: {step}"
                    )

                nested_pipeline_config = step.get("pipeline")
                if not nested_pipeline_config or not isinstance(
                    nested_pipeline_config, list
                ):
                    raise ValueError(
                        f"Step {i + 1}: 'foreach' step requires a nested 'pipeline' list, got: {nested_pipeline_config}. Step: {step}"
                    )

                sub_pipeline = FunctionalPipeline._build_pipeline_from_config(
                    nested_pipeline_config, nodes_map
                )
                pipeline.foreach(name=name, do=sub_pipeline, dependencies=dependencies)

            elif step_type == "branch":
                condition_ref = step.get("condition")
                if_true_config = step.get("if_true")
                if_false_config = step.get("if_false")

                if not condition_ref or not if_true_config or not if_false_config:
                    raise ValueError(
                        f"Step {i + 1}: 'branch' step requires 'condition' (string ref), 'if_true' (list), and 'if_false' (list). Step: {step}"
                    )
                if not isinstance(condition_ref, str):
                    raise ValueError(
                        f"Step {i + 1}: 'branch' step 'condition' reference must be a string name, got: {condition_ref}"
                    )
                if condition_ref not in nodes_map:
                    raise KeyError(
                        f"Step {i + 1}: 'branch' step condition '{condition_ref}' not found in provided nodes_map. Available: {list(nodes_map.keys())}"
                    )
                if not callable(nodes_map[condition_ref]):
                    raise ValueError(
                        f"Step {i + 1}: 'branch' step condition '{condition_ref}' resolved from nodes_map is not callable."
                    )
                if not isinstance(if_true_config, list) or not isinstance(
                    if_false_config, list
                ):
                    raise ValueError(
                        f"Step {i + 1}: 'branch' step 'if_true' and 'if_false' must be lists of steps. Step: {step}"
                    )

                condition_func = nodes_map[condition_ref]
                if_true_pipeline = FunctionalPipeline._build_pipeline_from_config(
                    if_true_config, nodes_map
                )
                if_false_pipeline = FunctionalPipeline._build_pipeline_from_config(
                    if_false_config, nodes_map
                )

                pipeline.branch(
                    condition=condition_func,
                    if_true=if_true_pipeline,
                    if_false=if_false_pipeline,
                    dependencies=dependencies,
                )

            else:
                raise ValueError(
                    f"Step {i + 1}: Unsupported step type '{step_type}'. Step: {step}"
                )

        return pipeline
