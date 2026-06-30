from langchain_core.language_models import BaseChatModel


async def run_monitoring_agent(llm: BaseChatModel, payload: dict) -> dict:
    # TODO: implement model monitoring and drift detection workflow with LangGraph
    return {"message": "Model monitoring agent not yet implemented", "payload": payload}
