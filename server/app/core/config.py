from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "DS Agent Assist"
    debug: bool = False
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
