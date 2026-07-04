"""核心抽象层：Embedding / LLM / 向量库 / 重排。

通过统一接口屏蔽底层提供方差异，支持云端(OpenAI 兼容 / DeepSeek)、
本地(Ollama / BGE)与测试(fake / echo)三态切换。
"""
