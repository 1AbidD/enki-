import json
import urllib.request
import urllib.error

url = "http://127.0.0.1:11434/api/chat"

# 1. Test tools definition
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Cari riwayat percakapan atau informasi masa lalu dalam database memori (FTS5).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Kata kunci pencarian"}
                },
                "required": ["query"]
            }
        }
    }
]

# 2. Test format schema
test_format = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string"}
    },
    "required": ["thought", "action"]
}

payload = {
    "model": "qwen2.5:3b",
    "messages": [
        {"role": "user", "content": "Cari informasi tentang proyek kemarin"}
    ],
    "tools": tools,
    "format": test_format,
    "stream": False,
    "options": {
        "temperature": 0.2
    }
}

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print("STATUS: SUCCESS")
        print("RESPONSE KEYS:", list(result.keys()))
        msg = result.get("message", {})
        print("MESSAGE ROLE:", msg.get("role"))
        print("CONTENT:", msg.get("content"))
        print("TOOL CALLS:", msg.get("tool_calls"))
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8")
    print(f"STATUS: HTTP_ERROR {e.code}")
    print(f"ERROR BODY: {err_body}")
except Exception as e:
    print(f"STATUS: EXCEPTION: {e}")
