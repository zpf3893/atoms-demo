"""DeepSeek HTTP client with bounded time, output validation, and redacted errors."""
import asyncio
import json

import httpx


class ModelError(Exception):
    pass


class DeepSeek:
    def __init__(self, settings):
        self.settings = settings

    async def complete(self, system, user, max_tokens=8000):
        if not self.settings.api_key:
            raise ModelError("尚未配置 DeepSeek API Key，请在本地 .env 中填写并重启服务。")
        try:
            # Total deadline also bounds servers that send endless whitespace keep-alives.
            async with asyncio.timeout(self.settings.timeout):
                async with httpx.AsyncClient(timeout=httpx.Timeout(self.settings.timeout, connect=15)) as client:
                    response = await client.post(
                        self.settings.base_url + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.settings.api_key},
                        json={"model": self.settings.model, "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ], "thinking": {"type": "disabled"}, "stream": False,
                            "response_format": {"type": "json_object"}, "max_tokens": max_tokens},
                    )
                    if response.status_code != 200:
                        messages = {401: "API Key 无效，请检查本地配置。", 402: "DeepSeek 账户余额不足。", 429: "模型服务限流，请稍后重试。", 400: "模型请求被拒绝，请检查模型名称和接口配置。", 404: "模型或接口不存在，请检查配置。"}
                        raise ModelError(messages.get(response.status_code, "模型服务暂时不可用，请稍后重试。"))
                    choice = response.json()["choices"][0]
                    if choice.get("finish_reason") != "stop":
                        raise ModelError("模型输出未完整结束，请缩小需求范围后重试。")
                    value = json.loads(choice["message"]["content"])
                    if not isinstance(value, dict):
                        raise ModelError("模型返回格式不正确，请重试。")
                    return value
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ModelError("模型响应超时，请稍后重试或简化需求。") from exc
        except httpx.RequestError as exc:
            raise ModelError("连接 DeepSeek 失败，请检查网络和接口地址。") from exc
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise ModelError("模型返回格式不正确，请重试。") from exc
