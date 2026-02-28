from datapizza.agents import Agent
from datapizza.clients.mock_client import MockClient
from datapizza.tools import tool


@tool
async def serve(customer: str):
    print(f"Serving customer: {customer}")


a = Agent(name="test_agent", client=MockClient(), tools={"serve": serve})


async def main():
    await a.run("Serve customer Alice")
