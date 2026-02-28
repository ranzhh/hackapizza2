from datapizza.tools.tools import Tool


class GoogleSearch(Tool):
    """
    Represents the Google Search tool for grounding in Gemini models.
    This is a special tool that doesn't represent a function to be called by the client,
    but instead enables a feature in the Gemini model.
    """

    def __init__(self):
        """Initializes the GoogleSearch tool."""
        super().__init__(
            name="google_search",
            description="Enables Google Search for grounding model responses.",
            func=None,  # No function is associated with this tool
            properties={},
            required=[],
        )


google_search_tool = GoogleSearch()
