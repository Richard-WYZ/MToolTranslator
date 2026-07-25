import requests
import time
from translation.config import fallback_models, ollama_host, system_prompts, think_setting
import translation.usage as token_usage


def _host():
    return ollama_host().rstrip("/")


def list_models():
    """获取本地模型列表，过滤嵌入模型"""
    host = _host()
    url = f"{host}/api/tags"
    try:
        resp = requests.get(url, timeout=(10, 60))
        resp.raise_for_status()
        data = resp.json()
        models = data.get("models", [])
        # 过滤掉含 'embed' 的模型
        filtered = [m for m in models if "embed" not in m.get("name", "").lower()]
        return filtered
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"无法连接到 Ollama 服务 ({host}): {e}")
    except requests.exceptions.Timeout:
        raise TimeoutError(f"获取模型列表超时 ({host})")
    except Exception as e:
        raise RuntimeError(f"获取模型列表失败: {e}")


def _build_prompt(text, terminology=None):
    if not text or not text.strip():
        return ""

    prompt = text
    if terminology:
        term_lines = []
        if isinstance(terminology, dict):
            for src, tgt in terminology.items():
                term_lines.append(f"{src} -> {tgt}")
        elif isinstance(terminology, list):
            for item in terminology:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    term_lines.append(f"{item[0]} -> {item[1]}")
                elif isinstance(item, str):
                    term_lines.append(item)
        elif isinstance(terminology, str):
            term_lines.append(terminology)
        if term_lines:
            term_block = "\n".join(term_lines)
            prompt = f"术语表:\n{term_block}\n\n请翻译以下文本:\n{text}"
    return prompt


def translate_once(model, text, system_prompt="", terminology=None, timeout=60, options=None, think=None, response_format=None):
    """发送一次翻译请求到 Ollama，不切换模型，不做策略重试。"""
    prompt = _build_prompt(text, terminology)
    if not prompt:
        return ""

    host = _host()
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
    }
    if think is None:
        think = think_setting()
    if think is not None:
        payload["think"] = think
    if response_format is not None:
        payload["format"] = response_format
    if options:
        payload["options"] = options

    try:
        request_started = token_usage.record_request_start("ollama", model)
        try:
            resp = requests.post(url, json=payload, timeout=(10, timeout))
        finally:
            token_usage.record_response_received("ollama", model, request_started)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("response", "").strip()
        if not result:
            raise ValueError("Ollama 返回空响应")
        return result
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"无法连接到 Ollama ({host}): {e}")
    except requests.exceptions.Timeout as e:
        raise TimeoutError(f"请求超时 (模型={model}): {e}")
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        if status == 404:
            raise RuntimeError(f"模型不存在: {model}")
        raise RuntimeError(f"HTTP 错误 (模型={model}): {e}")
    except Exception as e:
        raise RuntimeError(f"翻译失败 (模型={model}): {e}")


def translate(model, text, system_prompt="", terminology=None, timeout=60, options=None, think=None, response_format=None):
    """发送翻译请求到Ollama，支持超时重试和动态模型切换"""
    if not text or not text.strip():
        return ""

    # 构建尝试队列：主模型 + fallback_models（去重）
    models_to_try = [model]
    for fallback in fallback_models():
        if fallback != model and fallback not in models_to_try:
            models_to_try.append(fallback)

    last_error = None

    for attempt_model in models_to_try:
        for attempt in range(3):
            try:
                return translate_once(attempt_model, text, system_prompt=system_prompt, terminology=terminology, timeout=timeout, options=options, think=think, response_format=response_format)
            except requests.exceptions.ConnectionError as e:
                last_error = ConnectionError(f"无法连接到 Ollama ({_host()}): {e}")
            except requests.exceptions.Timeout as e:
                last_error = TimeoutError(
                    f"请求超时 (模型={attempt_model}, 尝试={attempt + 1}/3): {e}"
                )
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status == 404:
                    last_error = RuntimeError(f"模型不存在: {attempt_model}")
                    break  # 404 直接换下一个模型，不再重试
                last_error = RuntimeError(f"HTTP 错误 (模型={attempt_model}): {e}")
            except Exception as e:
                last_error = RuntimeError(f"翻译失败 (模型={attempt_model}): {e}")

            # 指数退避重试（1s, 2s, 4s）
            if attempt < 2:
                sleep_time = 2 ** attempt
                time.sleep(sleep_time)

    raise last_error if last_error else RuntimeError("翻译失败，所有模型和重试均已耗尽")


def get_system_prompts():
    """返回预定义的提示模板字典"""
    return system_prompts()
