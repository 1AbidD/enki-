import json
import re

with open('docs/dev_4dv4.json', 'r', encoding='utf-8') as f:
    dev_items = json.load(f)

with open('enki/core/capability_router.py', 'r', encoding='utf-8') as f:
    raw_source = f.read()

# Hapus bagian docstring dan balasan router teks
clean_source = raw_source.lower()
clean_source = re.sub(r'""".*?"""', '', clean_source, flags=re.DOTALL)
clean_source = re.sub(r"'''.*?'''", '', clean_source, flags=re.DOTALL)
clean_source = re.sub(r'\s+', ' ', clean_source)

matches = []
for item in dev_items:
    prompt = item['prompt'].lower()
    words = re.findall(r'\b\w+\b', prompt)
    if len(words) >= 3:
        for i in range(len(words) - 2):
            trigram = ' '.join(words[i:i+3])
            if trigram in clean_source:
                matches.append((item['intent'], prompt, trigram))

print(f"Total trigram matches found: {len(matches)}")
for m in matches:
    print("  MATCH:", m)
