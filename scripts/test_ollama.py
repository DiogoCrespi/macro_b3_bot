from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
try:
    res = client.chat.completions.create(
        model="qwen2.5:7b",
        messages=[{"role": "user", "content": 'Return JSON: {"status": "ok"}'}],
        response_format={"type": "json_object"}
    )
    print("Ollama Response:", res.choices[0].message.content)
except Exception as e:
    print("Ollama Error:", e)
