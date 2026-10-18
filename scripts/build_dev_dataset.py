import json
from collections import Counter

raw_4e = {
    'LIST_COMMANDS': [
        'kamu tuh sebenernya bisa apa sih',
        'command apa aja yg ada',
        'perintah apa saja yang tersedia',
        'fitur kamu apa aja',
        'apa yang bisa kamu lakukan',
        'kemampuanmu apa aja sih',
        'sebutin semua fitur yang kamu punya',
        'ada command apa di sini',
        'list perintah dong',
    ],
    'CAN_INTERNET': [
        'bisakah kamu membuka web internet ?',
        'kamu bisa browsing ga',
        'lu bisa nyari di google ga',
        'apa kamu terhubung ke internet',
        'bisa buka link ga',
        'kamu bisa akses website?',
    ],
    'CAN_SCREEN': [
        'lu bisa liat layar gue ga',
        'kamu bisa melihat tampilan monitorku?',
        'bisa baca apa yang ada di layar komputerku',
        'kamu bisa screenshot ga',
    ],
    'CAN_READ_FILE': [
        'bisa ga baca isi file',
        'kamu bisa buka file di folderku?',
        'apakah kamu bisa membaca dokumen',
        'bisa lihat isi folder ga',
    ],
    'CAN_TEST': [
        'kamu bisa ngetes kode?',
        'bisa jalanin unit test ga',
        'apakah kamu bisa menguji program saya',
    ],
    'CAN_SPEAK': [
        'bisa ngomong ga sih kamu',
        'kamu bisa bersuara?',
        'bisa ngobrol pake suara ga',
        'apa kamu punya suara',
    ],
    'CAN_REMEMBER': [
        'cara biar kamu inget namaku gimana',
        'kamu bisa nginget hal tentang aku ga',
        'gimana cara nyimpen fakta tentang gue',
        'apa kamu bisa ingat percakapan kemarin',
    ],
    'NONE': [
        'jelasin cara kerja internet',
        'aku mau beli monitor baru, rekomendasi dong',
        'tolong ingetin aku besok ada ujian',
        'gimana cara jalanin program python di laptop',
        'ceritain pengalaman paling lucu menurutmu',
        'bisa tolong jelasin apa itu TCP',
        'bisa bantu aku bikin kalimat bahasa inggris',
        'aku ga bisa liat layar hp ku, kenapa ya',
        'file excel ku rusak gimana benerinnya',
        'suara laptopku kecil banget',
        'internet di rumah lemot, ada tips',
        'namaku Abid, aku suka kopi',
        'kemarin aku lupa nama temanku',
        'gimana cara ngetes API di postman',
        'bisa ga sih manusia hidup tanpa internet',
        'web apa yang bagus buat belajar python',
        'kamu suka musik apa',
        'kode python ku error terus, bantuin dong',
    ]
}

dev_items = []
seen = set()

def add_item(intent, prompt, source):
    p_norm = prompt.strip()
    key = (intent, p_norm.lower())
    if key not in seen:
        seen.add(key)
        dev_items.append({'intent': intent, 'prompt': p_norm, 'source': source})

# 1. New 4E
for intent, prompts in raw_4e.items():
    for p in prompts:
        add_item(intent, p, '4E_new')

# 2. 4D-v1
v1_pos = [
    ('LIST_COMMANDS', 'kamu bisa ngapain aja'),
    ('LIST_COMMANDS', 'perintah apa saja yang ada'),
    ('CAN_INTERNET', 'bisakah kamu membuka web internet'),
    ('CAN_SCREEN', 'bisa lihat layar aku?'),
    ('CAN_READ_FILE', 'bisa baca file?'),
    ('CAN_TEST', 'bisa jalanin tes?'),
    ('CAN_SPEAK', 'bisa ngomong?'),
    ('CAN_REMEMBER', 'cara nyimpen sesuatu ke ingatanmu gimana'),
]
v1_neg = [
    'kurs dolar ke rupiah hari ini berapa',
    'siapa yang menang pertandingan bola semalam',
    'harga emas hari ini',
    'pagi',
    'jelaskan apa itu array',
    'ceritain hal lucu dong',
    'hari ini aku lulus ujian',
    'makasih ya',
    'kamu suka warna apa',
]
for intent, p in v1_pos:
    add_item(intent, p, '4D_v1')
for p in v1_neg:
    add_item('NONE', p, '4D_v1')

# 3. 4D-v2 (heldout_4dv2.json)
try:
    with open('docs/heldout_4dv2.json', 'r', encoding='utf-8') as f:
        v2_data = json.load(f)
    v2_hk_intents = {
        'HK1': 'LIST_COMMANDS',
        'HK2': 'LIST_COMMANDS',
        'HK3': 'CAN_INTERNET',
        'HK4': 'CAN_SCREEN',
        'HK5': 'CAN_READ_FILE',
        'HK6': 'CAN_TEST',
        'HK7': 'CAN_SPEAK',
        'HK8': 'CAN_REMEMBER',
    }
    for item in v2_data.get('HK', []):
        intent = v2_hk_intents[item['code']]
        add_item(intent, item['prompt'], '4D_v2')
    for group in ['HH', 'HU', 'HF']:
        for item in v2_data.get(group, []):
            add_item('NONE', item['prompt'], '4D_v2')
except Exception as e:
    print(f'Warning loading 4D-v2: {e}')

# 4. 4D-v3 (heldout_4dv3.json)
try:
    with open('docs/heldout_4dv3.json', 'r', encoding='utf-8') as f:
        v3_data = json.load(f)
    for item in v3_data.get('positif', []):
        add_item(item['intent'], item['prompt'], '4D_v3')
    for item in v3_data.get('negatif', []):
        add_item('NONE', item['prompt'], '4D_v3')
except Exception as e:
    print(f'Warning loading 4D-v3: {e}')

print(f'Total dev items compiled: {len(dev_items)}')
c = Counter(item['intent'] for item in dev_items)
for k, v in c.items():
    print(f'  {k}: {v}')

with open('docs/dev_4dv4.json', 'w', encoding='utf-8') as f:
    json.dump(dev_items, f, indent=2, ensure_ascii=False)

with open('docs/dev_4dv4.tsv', 'w', encoding='utf-8') as f:
    for item in dev_items:
        f.write(f"{item['intent']}\t{item['prompt']}\n")

print('Saved to docs/dev_4dv4.json and docs/dev_4dv4.tsv')
