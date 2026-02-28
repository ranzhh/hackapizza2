import re
from unittest.mock import MagicMock, PropertyMock

import pytest

from datapizza.core.clients import Client
from datapizza.modules.treebuilder.llm_treebuilder import LLMTreeBuilder

# Raw LLM output from the snippet file, including potential markdown fences
STRUCTURED_TEXT_FROM_FILE = """
```xml
<document>
<section>
<paragraph>
<sentence>ETF provider Betashares, which manages $30 billion in funds, reached an agreement to acquire Bendigo and Adelaide Bank's superannuation business, in its first venture into the superannuation sector.</sentence>
<sentence>Betashares said it was part of a longer-term strategy to expand the business into the broader financial sector.</sentence>
<sentence>Shares in Bendigo increased 0.6 per cent on the news.</sentence>
<sentence>REITS (up 0.4 per cent) was the strongest sector on the index as Goodman added 0.5 per cent and Dexus climbed 2.8 per cent.</sentence>
</paragraph>
<paragraph>
<sentence>The laggards Casino operator Star Entertainment Group's shares hit an all-time low of 60¢ after it raised $565 million.</sentence>
<sentence>They closed the session 16 per cent weaker at 63¢.</sentence>
<sentence>Star, which raised $800 million in February, has had to return to the market for fresh funding and is hoping to raise $750 million at a share price of 60¢ a share.</sentence>
</paragraph>
<paragraph>
<sentence>Meanwhile, healthcare heavyweight CSL shed 1.4 per cent, weighing down the healthcare sector and insurance companies IAG (down 2.6 per cent) and Suncorp (down 2 per cent) gave back some of their gains from Tuesday.</sentence>
<sentence>Gold miners Newcrest (down 2.1 per cent) and Evolution (down 3.5 per cent) were also among the biggest large-cap decliners after the spot gold price dropped 0.9 per cent overnight.</sentence>
<sentence>Information technology (down 1.1 per cent) was the weakest sector on the local bourse with WiseTech losing 1.4 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>The lowdown</sentence>
<sentence>Novus Capital senior client adviser Gary Glover said the Australian sharemarket was surprisingly resilient following a negative lead from Wall Street and the latest inflation data, with markets starting to wake up to the fact that interest rates could stay higher for longer.</sentence>
<sentence>"Considering the damage overnight in the US, Australian markets held on pretty well," he said.</sentence>
<sentence>"I thought it would be a bigger down day across the board."</sentence>
</paragraph>
<paragraph>
<sentence>Glover said the market was volatile but quite range-bound, similar to previous periods of high inflation in the 1940s and 1970s.</sentence>
<sentence>Elsewhere, Wall Street's ugly September got even worse on Tuesday, as a sharp drop for stocks brought them back to where they were in June.</sentence>
</paragraph>
<paragraph>
<sentence>The S&P 500 tumbled 1.5 per cent for its fifth loss in the last six days.</sentence>
<sentence>The Dow Jones dropped 1.1 per cent, and the Nasdaq composite lost 1.6 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>Loading September has brought a loss of 5.2 per cent so far for the S&P 500, putting it on track to be the worst month of the year by far, as the realisation sets in that the Federal Reserve will indeed keep interest rates high for a long time.</sentence>
<sentence>That growing understanding has sent yields in the bond market to their highest levels in more than a decade, which in turn has undercut prices for stocks and other investments.</sentence>
<sentence>Treasury yields rose again on Tuesday following a mixed batch of reports on the economy.</sentence>
<sentence>The yield on the 10-year Treasury edged up to 4.55 per cent from 4.54 per cent late on Monday and is near its highest level since 2007.</sentence>
<sentence>It's up sharply from about 3.5 per cent in May and from 0.5 per cent about three years ago.</sentence>
</paragraph>
<paragraph>
<sentence>One economic report on Tuesday showed confidence among consumers was weaker than economists expected.</sentence>
<sentence>That's concerning because strong spending by US households has been a bulwark keeping the economy out of a long-predicted recession.</sentence>
</paragraph>
<paragraph>
<sentence>Besides high interest rates, a long list of other worries is also tugging at Wall Street.</sentence>
<sentence>The most immediate is the threat of another US government shutdown as Capitol Hill threatens a stalemate that could shut off federal services across the country.</sentence>
</paragraph>
<paragraph>
<sentence>Loading Wall Street has dealt with such shutdowns in the past, and stocks have historically been turbulent in the run-up to them, according to Lori Calvasina, strategist at RBC Capital Markets.</sentence>
<sentence>After looking at the seven shutdowns that lasted 10 days or more since the 1970s, she found the S&P 500 dropped an average of roughly 10 per cent in the three months heading into them.</sentence>
<sentence>But stocks managed to hold up rather well during the shutdowns, falling an average of just 0.3 per cent, before rebounding meaningfully afterward.</sentence>
</paragraph>
<paragraph>
<sentence>Wall Street is also contending with higher oil prices, shaky economies around the world, a strike by US autoworkers that could put more upward pressure on inflation and a resumption of US student-loan repayments that could dent spending by households.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>On Wall Street, the vast majority of stocks fell under such pressures, including 90 per cent of those within the S&P 500.</sentence>
<sentence>Big Tech stocks tend to be among the hardest hit by high rates, and they were the heaviest weights on the index.</sentence>
<sentence>Apple fell 2.3 per cent and Microsoft lost 1.7 per cent.</sentence>
</paragraph>
<paragraph>
<sentence>Amazon tumbled 4 per cent after the Federal Trade Commission and 17 state attorneys general filed an antitrust lawsuit against it.</sentence>
<sentence>They accuse the e-commerce behemoth of using its dominant position to inflate prices on other platforms, overcharge sellers and stifle competition.</sentence>
</paragraph>
<paragraph>
<sentence>In China, concerns continued over heavily indebted real estate developer Evergrande.</sentence>
<sentence>The property market crisis there is dragging on China's economic growth and raising worries about financial instability.</sentence>
<sentence>France's CAC 40 fell 0.7 per cent, and Germany's DAX lost 1 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>Crude oil prices rose, adding to worries about inflation.</sentence>
<sentence>A barrel of benchmark US crude climbed 71¢ to $US90.39.</sentence>
<sentence>Brent crude, the international standard, added 67¢ to $US93.96 per barrel.</sentence>
</paragraph>
<paragraph>
<sentence>Tweet of the day</sentence>
<sentence>Quote of the day</sentence>
<sentence>"The Senate committees have the power to summons witnesses within Australia but have no enforceable powers for witnesses who are overseas," said Senator Bridget McKenzie as former Qantas boss Alan Joyce chose not to front the Senate select committee into the federal government's decision to reject extra flights from Qatar Airways due to "personal commitments".</sentence>
</paragraph>
</section>
</document>
```
"""

# Expected clean XML after _clean_llm_output
EXPECTED_CLEAN_XML = """<document>
<section>
<paragraph>
<sentence>ETF provider Betashares, which manages $30 billion in funds, reached an agreement to acquire Bendigo and Adelaide Bank's superannuation business, in its first venture into the superannuation sector.</sentence>
<sentence>Betashares said it was part of a longer-term strategy to expand the business into the broader financial sector.</sentence>
<sentence>Shares in Bendigo increased 0.6 per cent on the news.</sentence>
<sentence>REITS (up 0.4 per cent) was the strongest sector on the index as Goodman added 0.5 per cent and Dexus climbed 2.8 per cent.</sentence>
</paragraph>
<paragraph>
<sentence>The laggards Casino operator Star Entertainment Group's shares hit an all-time low of 60¢ after it raised $565 million.</sentence>
<sentence>They closed the session 16 per cent weaker at 63¢.</sentence>
<sentence>Star, which raised $800 million in February, has had to return to the market for fresh funding and is hoping to raise $750 million at a share price of 60¢ a share.</sentence>
</paragraph>
<paragraph>
<sentence>Meanwhile, healthcare heavyweight CSL shed 1.4 per cent, weighing down the healthcare sector and insurance companies IAG (down 2.6 per cent) and Suncorp (down 2 per cent) gave back some of their gains from Tuesday.</sentence>
<sentence>Gold miners Newcrest (down 2.1 per cent) and Evolution (down 3.5 per cent) were also among the biggest large-cap decliners after the spot gold price dropped 0.9 per cent overnight.</sentence>
<sentence>Information technology (down 1.1 per cent) was the weakest sector on the local bourse with WiseTech losing 1.4 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>The lowdown</sentence>
<sentence>Novus Capital senior client adviser Gary Glover said the Australian sharemarket was surprisingly resilient following a negative lead from Wall Street and the latest inflation data, with markets starting to wake up to the fact that interest rates could stay higher for longer.</sentence>
<sentence>"Considering the damage overnight in the US, Australian markets held on pretty well," he said.</sentence>
<sentence>"I thought it would be a bigger down day across the board."</sentence>
</paragraph>
<paragraph>
<sentence>Glover said the market was volatile but quite range-bound, similar to previous periods of high inflation in the 1940s and 1970s.</sentence>
<sentence>Elsewhere, Wall Street's ugly September got even worse on Tuesday, as a sharp drop for stocks brought them back to where they were in June.</sentence>
</paragraph>
<paragraph>
<sentence>The S&amp;P 500 tumbled 1.5 per cent for its fifth loss in the last six days.</sentence>
<sentence>The Dow Jones dropped 1.1 per cent, and the Nasdaq composite lost 1.6 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>Loading September has brought a loss of 5.2 per cent so far for the S&amp;P 500, putting it on track to be the worst month of the year by far, as the realisation sets in that the Federal Reserve will indeed keep interest rates high for a long time.</sentence>
<sentence>That growing understanding has sent yields in the bond market to their highest levels in more than a decade, which in turn has undercut prices for stocks and other investments.</sentence>
<sentence>Treasury yields rose again on Tuesday following a mixed batch of reports on the economy.</sentence>
<sentence>The yield on the 10-year Treasury edged up to 4.55 per cent from 4.54 per cent late on Monday and is near its highest level since 2007.</sentence>
<sentence>It's up sharply from about 3.5 per cent in May and from 0.5 per cent about three years ago.</sentence>
</paragraph>
<paragraph>
<sentence>One economic report on Tuesday showed confidence among consumers was weaker than economists expected.</sentence>
<sentence>That's concerning because strong spending by US households has been a bulwark keeping the economy out of a long-predicted recession.</sentence>
</paragraph>
<paragraph>
<sentence>Besides high interest rates, a long list of other worries is also tugging at Wall Street.</sentence>
<sentence>The most immediate is the threat of another US government shutdown as Capitol Hill threatens a stalemate that could shut off federal services across the country.</sentence>
</paragraph>
<paragraph>
<sentence>Loading Wall Street has dealt with such shutdowns in the past, and stocks have historically been turbulent in the run-up to them, according to Lori Calvasina, strategist at RBC Capital Markets.</sentence>
<sentence>After looking at the seven shutdowns that lasted 10 days or more since the 1970s, she found the S&amp;P 500 dropped an average of roughly 10 per cent in the three months heading into them.</sentence>
<sentence>But stocks managed to hold up rather well during the shutdowns, falling an average of just 0.3 per cent, before rebounding meaningfully afterward.</sentence>
</paragraph>
<paragraph>
<sentence>Wall Street is also contending with higher oil prices, shaky economies around the world, a strike by US autoworkers that could put more upward pressure on inflation and a resumption of US student-loan repayments that could dent spending by households.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>On Wall Street, the vast majority of stocks fell under such pressures, including 90 per cent of those within the S&amp;P 500.</sentence>
<sentence>Big Tech stocks tend to be among the hardest hit by high rates, and they were the heaviest weights on the index.</sentence>
<sentence>Apple fell 2.3 per cent and Microsoft lost 1.7 per cent.</sentence>
</paragraph>
<paragraph>
<sentence>Amazon tumbled 4 per cent after the Federal Trade Commission and 17 state attorneys general filed an antitrust lawsuit against it.</sentence>
<sentence>They accuse the e-commerce behemoth of using its dominant position to inflate prices on other platforms, overcharge sellers and stifle competition.</sentence>
</paragraph>
<paragraph>
<sentence>In China, concerns continued over heavily indebted real estate developer Evergrande.</sentence>
<sentence>The property market crisis there is dragging on China's economic growth and raising worries about financial instability.</sentence>
<sentence>France's CAC 40 fell 0.7 per cent, and Germany's DAX lost 1 per cent.</sentence>
</paragraph>
</section>
<section>
<paragraph>
<sentence>Crude oil prices rose, adding to worries about inflation.</sentence>
<sentence>A barrel of benchmark US crude climbed 71¢ to $US90.39.</sentence>
<sentence>Brent crude, the international standard, added 67¢ to $US93.96 per barrel.</sentence>
</paragraph>
<paragraph>
<sentence>Tweet of the day</sentence>
<sentence>Quote of the day</sentence>
<sentence>"The Senate committees have the power to summons witnesses within Australia but have no enforceable powers for witnesses who are overseas," said Senator Bridget McKenzie as former Qantas boss Alan Joyce chose not to front the Senate select committee into the federal government's decision to reject extra flights from Qatar Airways due to "personal commitments".</sentence>
</paragraph>
</section>
</document>"""


REJOINED_TEXT = """ETF provider Betashares, which manages $30 billion in funds, reached an agreement to acquire Bendigo and Adelaide Bank\u2019s superannuation business, in its first venture into the superannuation sector. Betashares said it was part of a longer-term strategy to expand the business into the broader financial sector. Shares in Bendigo increased 0.6 per cent on the news. REITS (up 0.4 per cent) was the strongest sector on the index as Goodman added 0.5 per cent and Dexus climbed 2.8 per cent. The laggards Casino operator Star Entertainment Group\u2019s shares hit an all-time low of 60\u00a2 after it raised $565 million. They closed the session 16 per cent weaker at 63\u00a2. Star, which raised $800 million in February, has had to return to the market for fresh funding and is hoping to raise $750 million at a share price of 60\u00a2 a share.\n\nMeanwhile, healthcare heavyweight CSL shed 1.4 per cent, weighing down the healthcare sector and insurance companies IAG (down 2.6 per cent) and Suncorp (down 2 per cent) gave back some of their gains from Tuesday. Gold miners Newcrest (down 2.1 per cent) and Evolution (down 3.5 per cent) were also among the biggest large-cap decliners after the spot gold price dropped 0.9 per cent overnight. Information technology (down 1.1 per cent) was the weakest sector on the local bourse with WiseTech losing 1.4 per cent. The lowdown\n\nNovus Capital senior client adviser Gary Glover said the Australian sharemarket was surprisingly resilient following a negative lead from Wall Street and the latest inflation data, with markets starting to wake up to the fact that interest rates could stay higher for longer. \u201cConsidering the damage overnight in the US, Australian markets held on pretty well,\u201d he said. \u201cI thought it would be a bigger down day across the board.\u201d Glover said the market was volatile but quite range-bound, similar to previous periods of high inflation in the 1940s and 1970s. Elsewhere, Wall Street\u2019s ugly September got even worse on Tuesday, as a sharp drop for stocks brought them back to where they were in June. The S&P 500 tumbled 1.5 per cent for its fifth loss in the last six days. The Dow Jones dropped 1.1 per cent, and the Nasdaq composite lost 1.6 per cent.\n\nLoading September has brought a loss of 5.2 per cent so far for the S&P 500, putting it on track to be the worst month of the year by far, as the realisation sets in that the Federal Reserve will indeed keep interest rates high for a long time. That growing understanding has sent yields in the bond market to their highest levels in more than a decade, which in turn has undercut prices for stocks and other investments. Treasury yields rose again on Tuesday following a mixed batch of reports on the economy. The yield on the 10-year Treasury edged up to 4.55 per cent from 4.54 per cent late on Monday and is near its highest level since 2007. It\u2019s up sharply from about 3.5 per cent in May and from 0.5 per cent about three years ago. One economic report on Tuesday showed confidence among consumers was weaker than economists expected. That\u2019s concerning because strong spending by US households has been a bulwark keeping the economy out of a long-predicted recession.\n\nBesides high interest rates, a long list of other worries is also tugging at Wall Street. The most immediate is the threat of another US government shutdown as Capitol Hill threatens a stalemate that could shut off federal services across the country. Loading Wall Street has dealt with such shutdowns in the past, and stocks have historically been turbulent in the run-up to them, according to Lori Calvasina, strategist at RBC Capital Markets. After looking at the seven shutdowns that lasted 10 days or more since the 1970s, she found the S&P 500 dropped an average of roughly 10 per cent in the three months heading into them. But stocks managed to hold up rather well during the shutdowns, falling an average of just 0.3 per cent, before rebounding meaningfully afterward. Wall Street is also contending with higher oil prices, shaky economies around the world, a strike by US autoworkers that could put more upward pressure on inflation and a resumption of US student-loan repayments that could dent spending by households.\n\nOn Wall Street, the vast majority of stocks fell under such pressures, including 90 per cent of those within the S&P 500. Big Tech stocks tend to be among the hardest hit by high rates, and they were the heaviest weights on the index. Apple fell 2.3 per cent and Microsoft lost 1.7 per cent. Amazon tumbled 4 per cent after the Federal Trade Commission and 17 state attorneys general filed an antitrust lawsuit against it. They accuse the e-commerce behemoth of using its dominant position to inflate prices on other platforms, overcharge sellers and stifle competition. In China, concerns continued over heavily indebted real estate developer Evergrande. The property market crisis there is dragging on China\u2019s economic growth and raising worries about financial instability. France\u2019s CAC 40 fell 0.7 per cent, and Germany\u2019s DAX lost 1 per cent.\n\nCrude oil prices rose, adding to worries about inflation. A barrel of benchmark US crude climbed 71\u00a2 to $US90.39. Brent crude, the international standard, added 67\u00a2 to $US93.96 per barrel. Tweet of the day Quote of the day \u201cThe Senate committees have the power to summons witnesses within Australia but have no enforceable powers for witnesses who are overseas,\u201d said Senator Bridget McKenzie as former Qantas boss Alan Joyce chose not to front the Senate select committee into the federal government\u2019s decision to reject extra flights from Qatar Airways due to \u201cpersonal commitments\u201d."""


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """Fixture to create a mock LLM client."""
    client = MagicMock(spec=Client)
    # Mock the 'invoke' method to return a MagicMock
    mock_response = MagicMock()
    type(mock_response).text = PropertyMock(return_value=STRUCTURED_TEXT_FROM_FILE)
    client.invoke.return_value = mock_response
    return client


def are_equal_ignore_whitespace_punctuation_case(str1, str2):
    cleaned_str1 = re.sub(r"[^A-Za-z0-9]+", "", str1).lower()
    cleaned_str2 = re.sub(r"[^A-Za-z0-9]+", "", str2).lower()
    return cleaned_str1 == cleaned_str2


def test_clean_llm_output(mock_llm_client):
    """Test the _clean_llm_output method directly."""
    builder = LLMTreeBuilder(client=mock_llm_client)
    cleaned_output = builder._clean_llm_output(STRUCTURED_TEXT_FROM_FILE)

    # Check if markdown fences are removed
    assert "```xml" not in cleaned_output
    assert "```" not in cleaned_output

    # Check if leading/trailing whitespace is removed (relative to <document> tags)
    assert cleaned_output.startswith("<document>")
    assert cleaned_output.endswith("</document>")

    # Check if specific entity is correctly escaped (e.g., S&P 500)
    assert "<sentence>The S&amp;P 500 tumbled 1.5 per cent" in cleaned_output

    # Very basic check to ensure it still looks like XML
    assert "<section>" in cleaned_output
    assert "<paragraph>" in cleaned_output
    assert "<sentence>" in cleaned_output


def test_build_tree_with_mock_llm_output(mock_llm_client):
    """Test build_tree using the mocked LLM output."""
    builder = LLMTreeBuilder(client=mock_llm_client)
    input_text = "This is some dummy input text."  # Actual input doesn't matter here
    root_node = builder.parse(input_text)

    assert are_equal_ignore_whitespace_punctuation_case(
        root_node.content, REJOINED_TEXT
    )
