from jinja2 import Template

from datapizza.memory import Memory
from datapizza.modules.prompt import ChatPromptTemplate
from datapizza.type import ROLE, Chunk, TextBlock


def test_chat_prompt_template_with_no_memory():
    user_prompt_template = "this is a user prompt: {{ user_prompt }}"
    retrieval_prompt_template = (
        "{% for chunk in chunks %}CHUNK: {{ chunk.text }}{% endfor %}"
    )

    chat_prompt_template = ChatPromptTemplate(
        user_prompt_template, retrieval_prompt_template
    )

    chunks = [
        Chunk(
            id="1",
            text="This is a chunk of text",
            metadata={"source": "test"},
        )
    ]
    user_prompt = "What is the capital of France?"

    res = chat_prompt_template.format(chunks=chunks, user_prompt=user_prompt)
    assert res is not None

    assert res.memory[0].role == ROLE.USER
    assert res.memory[0].blocks[0].content == Template(user_prompt_template).render(
        user_prompt=user_prompt
    )

    assert res.memory[2].role == ROLE.TOOL
    assert res.memory[2].blocks[0].result == Template(retrieval_prompt_template).render(
        chunks=chunks
    )


def test_chat_prompt_template_with_memory():
    user_prompt_template = "this is a user prompt: {{ user_prompt }}"
    retrieval_prompt_template = (
        "{% for chunk in chunks %}CHUNK: {{ chunk.text }}{% endfor %}"
    )
    user_prompt = "What is the capital of France?"

    chat_prompt_template = ChatPromptTemplate(
        user_prompt_template, retrieval_prompt_template
    )

    memory = Memory()
    memory.add_turn(TextBlock(content="This is an old message"), ROLE.USER)
    memory.add_turn(TextBlock(content="This is an old message"), ROLE.ASSISTANT)

    chunks = [
        Chunk(id="1", text="This is a chunk of text", metadata={"source": "test"})
    ]

    res = chat_prompt_template.format(
        memory=memory,
        chunks=chunks,
        user_prompt=user_prompt,
    )
    assert res is not None

    assert res.memory[2].role == ROLE.USER
    assert res.memory[2].blocks[0].content == Template(user_prompt_template).render(
        user_prompt=user_prompt
    )

    assert res.memory[4].role == ROLE.TOOL
    assert res.memory[4].blocks[0].result == Template(retrieval_prompt_template).render(
        chunks=chunks
    )
