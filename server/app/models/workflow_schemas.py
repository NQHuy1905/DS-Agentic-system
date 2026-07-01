from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    # Accept the client's camelCase `apiKey` as well as snake_case `api_key`,
    # so the TypeScript client can send its native field name unchanged.
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    model: str
    api_key: str = Field(validation_alias=AliasChoices("api_key", "apiKey"))


class WorkflowRequest(BaseModel):
    llm_config: LLMConfig
    payload: dict


class WorkflowResponse(BaseModel):
    workflow: str
    result: dict
    status: str = "success"
