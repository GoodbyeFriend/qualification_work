from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_user_ids: str = ""
    data_dir: str = "/data"
    tz: str = "Europe/Moscow"
    ollama_base_url: str = "http://nginx-ollama:11434"
    ollama_chat_model: str = "Qwen3-4B-q8"        # твоя Qwen
    ollama_embed_model: str = "nomic-embed-text-v2-moe"  # пример (можешь заменить)    
    ollama_rerank_model: str = "dengcao/Qwen3-Reranker-0.6B:Q8_0"
    database_url: str
    chroma_host: str = "chroma"
    chroma_port: int = 8000
    @property
    def admin_ids(self) -> set[int]:
        if not self.admin_user_ids.strip():
            return set()
        return {int(x.strip()) for x in self.admin_user_ids.split(",") if x.strip()}


settings = Settings()
