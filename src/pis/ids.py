import hashlib


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def conversation_id(provider: str, provider_conversation_id: str) -> str:
    return f"conv_{_h(provider + ':' + provider_conversation_id)}"


def message_id(conv_id: str, key: str) -> str:
    return f"msg_{_h(conv_id + ':' + key)}"


def turn_id(session_id: str, n: int) -> str:
    return f"turn_{_h(session_id + ':' + str(n))}"


def repo_id(full_name: str) -> str:
    return f"repo_{_h(full_name)}"


def git_object_id(rid: str, object_type: str, object_key: str) -> str:
    return f"git_{_h(rid + ':' + object_type + ':' + object_key)}"


def tool_event_id(tid: str, index: int) -> str:
    return f"tool_{_h(tid + ':' + str(index))}"
