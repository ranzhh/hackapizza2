from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from datapizza.core.clients import Client, ClientResponse
from datapizza.memory.memory import Memory
from datapizza.modules.metatagger.keyword_metatagger import KeywordMetatagger
from datapizza.type import Chunk, StructuredBlock


@pytest.fixture
def mock_client():
    client = Mock(spec=Client)
    return client


@pytest.fixture
def sample_chunks():
    return [
        Chunk(
            id="1",
            text="This is a test document about artificial intelligence.",
            metadata={},
        ),
        Chunk(
            id="2",
            text="Machine learning and deep learning are important topics.",
            metadata={},
        ),
    ]


@pytest.fixture
def keyword_metatagger(mock_client):
    return KeywordMetatagger(
        client=mock_client,
        max_workers=2,
        system_prompt="Extract keywords from the text.",
        user_prompt="Please identify key terms.",
        keyword_name="keywords",
    )


def test_keyword_metatagger_initialization(keyword_metatagger):
    assert keyword_metatagger.max_workers == 2
    assert keyword_metatagger.system_prompt == "Extract keywords from the text."
    assert keyword_metatagger.user_prompt == "Please identify key terms."
    assert keyword_metatagger.keyword_name == "keywords"


def test_process_chunk(keyword_metatagger, mock_client):
    class KeywordMetataggerOutput(BaseModel):
        keywords: list[str]

    # Setup mock response
    mock_response = ClientResponse(
        content=[
            StructuredBlock(
                content=KeywordMetataggerOutput(keywords=["AI", "machine learning"])
            )
        ]
    )
    mock_client.structured_response.return_value = mock_response

    # Create test chunk
    chunk = Chunk(id="1", text="Test text", metadata={})

    # Process chunk
    result = keyword_metatagger._process_chunk(chunk)

    # Verify results
    assert result.id == "1"
    assert result.text == "Test text"
    assert result.metadata["keywords"] == ["AI", "machine learning"]

    # Verify client was called correctly
    mock_client.structured_response.assert_called_once()
    call_args = mock_client.structured_response.call_args[1]
    assert call_args["input"] == "Test text"
    assert call_args["system_prompt"] == "Extract keywords from the text."
    assert isinstance(call_args["memory"], Memory)
    assert (
        call_args["memory"].memory[0].blocks[0].content == "Please identify key terms."
    )


def test_process_chunks_concurrent(keyword_metatagger, mock_client, sample_chunks):
    class KeywordMetataggerOutput(BaseModel):
        keywords: list[str]

    # Setup mock responses
    mock_response1 = ClientResponse(
        content=[
            StructuredBlock(content=KeywordMetataggerOutput(keywords=["AI", "test"]))
        ]
    )
    mock_response2 = ClientResponse(
        content=[
            StructuredBlock(
                content=KeywordMetataggerOutput(
                    keywords=["machine learning", "deep learning"]
                )
            )
        ]
    )
    mock_client.structured_response.side_effect = [mock_response1, mock_response2]

    # Process chunks
    results = keyword_metatagger._process(sample_chunks)

    # Verify results
    assert len(results) == 2
    assert results[0].metadata["keywords"] == ["AI", "test"]
    assert results[1].metadata["keywords"] == ["machine learning", "deep learning"]
    assert mock_client.structured_response.call_count == 2
