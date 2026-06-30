from fastapi import APIRouter
from app.models.workflow_schemas import WorkflowRequest, WorkflowResponse
from app.core.llm_factory import create_llm
from app.agents.feature_eng_agent import run_feature_eng_agent

router = APIRouter(prefix="/feature-engineering", tags=["feature-engineering"])


@router.post("/run", response_model=WorkflowResponse)
async def run_feature_engineering(request: WorkflowRequest):
    llm = create_llm(
        request.llm_config.provider,
        request.llm_config.model,
        request.llm_config.api_key,
    )
    result = await run_feature_eng_agent(llm, request.payload)
    return WorkflowResponse(workflow="feature-engineering", result=result)
