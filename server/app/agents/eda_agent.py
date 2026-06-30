from langchain_core.language_models import BaseChatModel


async def run_eda_agent(llm: BaseChatModel, payload: dict) -> dict:
    # TODO: implement EDA agentic workflow with LangGraph
    return {"message": "EDA agent not yet implemented", "payload": payload}
