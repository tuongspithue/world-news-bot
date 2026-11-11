"""Dạo quanh thế giới - dựng Reel: khung PNG (Drive, liên kết công khai) + lời đọc edge-tts (tiếng Anh/Việt) + nhạc nền -> MP4 9:16 -> đăng Facebook."""
import os, sys, json, random, subprocess, asyncio, time
import requests
import edge_tts

P = json.loads(os.environ.get('PAYLOAD') or '{}')
for k in ('folder_id', 'page_id', 'token_secret'):
    if not P.get(k):
        sys.exit('Thiếu trường bắt buộc trong payload: ' + k)
PAGE_ID = str(P['page_id'])
TOKEN = os.environ.get(P['token_secret'], '')
if not TOKEN:
    sys.exit('Không có secret ' + P['token_secret'])
GV = 'https://graph.facebook.com/v23.0'
W = '/tmp/reel'; os.makedirs(W, exist_ok=True)


def dl(fid, dest):
    """Tải tệp Drive đã chia sẻ 'ai có liên kết' (không cần khoá Google)."""
    for u in (f'https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t',
              f'https://drive.google.com/uc?export=download&id={fid}&confirm=t'):
        try:
            r = requests.get(u, timeout=120)
            head = r.content[:20].lstrip().lower()
            if r.ok and len(r.content) > 500 and not head.startswith(b'<!doctype') and not head.startswith(b'<html'):
                open(dest, 'wb').write(r.content); return
        except Exception as e:
            print('Tải lỗi', fid, e)
    raise RuntimeError('Không tải được tệp Drive ' + fid)


def dur(path):
    o = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path]).decode().strip()
    return float(o)


# 1) khung hình: folder_id là danh sách mã tệp (phân cách bằng dấu phẩy, đã sắp theo thứ tự)
ids = [x.strip() for x in str(P['folder_id']).split(',') if x.strip()]
if not ids:
    sys.exit('Không có khung hình')
fpaths = []
for i, fid in enumerate(ids):
    p = f'{W}/f{i:02d}.png'; dl(fid, p); fpaths.append(p)

# 2) lời đọc: phần tử "@voice:", "@rate:" là cấu hình, còn lại đọc theo từng khung
nar = P.get('narration') or []
if isinstance(nar, str):
    nar = [nar]
voice, rate = 'vi-VN-HoaiMyNeural', '+0%'
lines = []
for x in nar:
    x = str(x)
    if x.startswith('@voice:'): voice = x[7:].strip()
    elif x.startswith('@rate:'): rate = x[6:].strip()
    else: lines.append(x)

SEC_DEFAULT = float(P.get('seconds') or 20)
MIN_FRAME, PAD, MIN_TOTAL, MAX_TOTAL = 2.2, 0.35, 12.0, 75.0


async def tts(text, out):
    await edge_tts.Communicate(text, voice, rate=rate).save(out)

durs, auds = [], []
for i, p in enumerate(fpaths):
    t = lines[i].strip() if i < len(lines) else ''
    a = None
    if t:
        a = f'{W}/a{i:02d}.mp3'
        try:
            asyncio.run(tts(t, a)); d = dur(a) + PAD
        except Exception as e:
            print('TTS lỗi khung', i, e); a = None; d = SEC_DEFAULT / len(fpaths)
    else:
        d = SEC_DEFAULT / len(fpaths)
    auds.append(a); durs.append(max(MIN_FRAME, d))
tot = sum(durs)
if tot < MIN_TOTAL:
    durs = [d * MIN_TOTAL / tot for d in durs]
elif tot > MAX_TOTAL:
    durs = [d * MAX_TOTAL / tot for d in durs]
tot = sum(durs)
print('Tổng thời lượng', round(tot, 1), 's; giọng', voice, rate)

# 3) dựng từng đoạn hình (zoom nhẹ) rồi ghép
segs = []
for i, p in enumerate(fpaths):
    s = f'{W}/s{i:02d}.mp4'; n = int(durs[i] * 30)
    vf = f"scale=1296:2304,zoompan=z='min(zoom+0.0006,1.06)':d={n}:s=1080x1920:fps=30,format=yuv420p"
    subprocess.check_call(['ffmpeg', '-y', '-loglevel', 'error', '-loop', '1', '-i', p, '-vf', vf, '-t', f'{durs[i]:.2f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', s])
    segs.append(s)
open(f'{W}/list.txt', 'w').write(''.join(f"file '{s}'\n" for s in segs))
subprocess.check_call(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', f'{W}/list.txt', '-c', 'copy', f'{W}/video.mp4'])

# 4) âm thanh: lời đọc đặt đúng đầu từng khung + nhạc nền nhỏ
inputs, filt, labels, t0 = [], [], [], 0.0
for i, a in enumerate(auds):
    if a:
        inputs += ['-i', a]; k = len(labels)
        filt.append(f'[{k+1}:a]adelay={int(t0*1000)}|{int(t0*1000)},volume=1.6[v{k}]'); labels.append(f'[v{k}]')
    t0 += durs[i]
music = None
if P.get('music_folder_id'):
    try:
        music = f'{W}/music.mp3'; dl(str(P['music_folder_id']).strip(), music)
    except Exception as e:
        print('Nhạc lỗi:', e); music = None
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', f'{W}/video.mp4'] + inputs
if music:
    cmd += ['-stream_loop', '-1', '-i', music]
mi = len(labels) + 1
parts = list(filt)
if labels and music:
    parts.append(f'[{mi}:a]volume=0.20,atrim=0:{tot:.2f}[bg]')
    parts.append(''.join(labels) + '[bg]' + f'amix=inputs={len(labels)+1}:duration=longest:normalize=0,atrim=0:{tot:.2f}[aout]')
elif labels:
    parts.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=longest:normalize=0,atrim=0:{tot:.2f}[aout]')
elif music:
    parts.append(f'[{mi}:a]volume=0.5,atrim=0:{tot:.2f}[aout]')
out = f'{W}/final.mp4'
if parts:
    cmd += ['-filter_complex', ';'.join(parts), '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-shortest', out]
else:
    cmd += ['-c', 'copy', out]
subprocess.check_call(cmd)

# 5) đăng Reel (hoặc Story video nếu kind=story)
kind = P.get('kind', 'reel')
ep = 'video_stories' if kind == 'story' else 'video_reels'
r = requests.post(f'{GV}/{PAGE_ID}/{ep}', data={'upload_phase': 'start', 'access_token': TOKEN}, timeout=60); r.raise_for_status()
vid, up = r.json()['video_id'], r.json()['upload_url']
size = os.path.getsize(out)
with open(out, 'rb') as fh:
    u = requests.post(up, headers={'Authorization': 'OAuth ' + TOKEN, 'offset': '0', 'file_size': str(size)}, data=fh, timeout=600)
u.raise_for_status()
fin = {'upload_phase': 'finish', 'video_id': vid, 'access_token': TOKEN, 'video_state': 'PUBLISHED'}
if kind != 'story':
    fin['description'] = P.get('caption', '')
f = requests.post(f'{GV}/{PAGE_ID}/{ep}', data=fin, timeout=120)
print(f.status_code, f.text[:300]); f.raise_for_status()
for _ in range(12):
    s = requests.get(f'{GV}/{vid}', params={'fields': 'status', 'access_token': TOKEN}, timeout=30).json()
    print(s)
    if (s.get('status') or {}).get('video_status') in ('ready', 'error'): break
    time.sleep(10)
