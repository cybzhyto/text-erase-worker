"""Replace frame ranges of a video with re-made patches (no audio): splice.py BASE OUT PATCH:START [PATCH:START ...]
Each PATCH replaces BASE frames START .. START+len(PATCH)-1. Re-encodes once (crf 12)."""
import subprocess, sys
base, out, specs = sys.argv[1], sys.argv[2], sorted(((s.rsplit(':', 1)[0], int(s.rsplit(':', 1)[1])) for s in sys.argv[3:]), key=lambda x: x[1])
def nfr(f): return int(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', f]).decode().strip())
ins, fc, segs, cur = ['-i', base], [], [], 0
for k, (p, s) in enumerate(specs, 1):
    ins += ['-i', p]; n = nfr(p)
    if s > cur: fc.append(f'[0:v]trim=start_frame={cur}:end_frame={s},setpts=PTS-STARTPTS[a{k}]'); segs.append(f'[a{k}]')
    fc.append(f'[{k}:v]setpts=PTS-STARTPTS[p{k}]'); segs.append(f'[p{k}]'); cur = s + n
fc.append(f'[0:v]trim=start_frame={cur},setpts=PTS-STARTPTS[z]'); segs.append('[z]')
fc.append(''.join(segs) + f'concat=n={len(segs)}:v=1:a=0[v]')
subprocess.check_call(['ffmpeg', '-v', 'error', '-y'] + ins + ['-filter_complex', ';'.join(fc), '-map', '[v]', '-c:v', 'libx264', '-preset', 'medium',
                       '-crf', '12', '-pix_fmt', 'yuv420p', '-r', '30', out])
print('spliced', len(specs), 'patches ->', out, nfr(out), 'frames')
