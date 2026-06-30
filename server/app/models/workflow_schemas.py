from pydantic import BaseModel


class LLMConfig(BaseModel):
    provider: str
    model: str
    api_key: str


class WorkflowRequest(BaseModel):
    llm_config: LLMConfig
    payload: dict


class WorkflowResponse(BaseModel):
    workflow: str
    result: dict
    status: str = "success"
