"""Render the device face (presentation view) as HTML for a given page / tab state, like Live would show it."""
import json, re, sys, html
sys.path.insert(0, sys.argv[1])
from build_ml185 import read_chunks, parse_mxc
amxd, out, page, tab, src = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
FOLD = len(sys.argv) > 7 and sys.argv[7] == 'fold'   # settings column folded: hide it, shift the rest 174 left
P = json.loads(parse_mxc(read_chunks(open(amxd, 'rb').read())[2][1])[0]['data'].rstrip(b'\0'))['patcher']
B = {b['box']['id']: b['box'] for b in P['boxes']}
groups = {}
for x in B.values():
    if x['maxclass'] == 'message' and x.get('text', '').startswith('uigroup '):
        g = x['text'].split()[1]
        for nm in re.findall(r'"([^"]+)"', x['text']): groups.setdefault(nm, []).append(g)
on = {'steps': page == 0, 'note': page == 0, 'mod': page == 1, 'notmod': page != 1, 'tab:random': page == 0,
      'tab:mod': page == 1, 'own': src != 1, 'live': src == 1, 'micro': False, 'left': not FOLD}
def visible(x):
    nm = x.get('varname')
    if nm in groups: return all(on[g] for g in groups[nm])
    return not x.get('hidden')
init = {}
for x in B.values():
    v = x.get('saved_attribute_attributes', {}).get('valueof', {})
    init[x['id']] = (v.get('parameter_initial') or [v.get('parameter_mmin', 0)])[0], v
W, H, Z = int(P['devicewidth']), 169, 2
if FOLD:
    W -= 174
    for x in B.values():
        if x.get('presentation') and x['presentation_rect'][0] >= 178:
            x['presentation_rect'] = [x['presentation_rect'][0] - 174] + x['presentation_rect'][1:]
o = ['<html><body style="margin:0;background:#202020"><div style="position:relative;width:%dpx;height:%dpx;background:#474747;font-family:Helvetica,Arial;transform:scale(%d);transform-origin:0 0">' % (W, H, Z)]
def div(r, style, inner=''):
    o.append('<div style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;box-sizing:border-box;%s">%s</div>' % (r[0], r[1], r[2], r[3], style, inner))
NN = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
order = sorted(B.values(), key=lambda x: (0 if x['maxclass'] in ('panel', 'live.line') else 1))
for x in order:
    if not x.get('presentation') or not visible(x): continue
    r = x['presentation_rect']; mc = x['maxclass']; val, v = init[x['id']]
    if mc == 'panel': div(r, 'background:rgba(0,0,0,0.12)')
    elif mc == 'live.line': div([r[0] + r[2] / 2 - 0.5, r[1], 1, r[3]], 'background:#333')
    elif mc == 'comment': div(r, 'font-size:%spx;color:#bbb;line-height:1.1;padding-top:1px' % (x.get('fontsize', 8) * 0.9), html.escape(x.get('text', '')))
    elif mc == 'live.tab':
        e = v.get('parameter_enum', []); rows = 2 if len(e) == 8 else 1; per = len(e) // rows
        cells = ''.join('<span style="flex:0 0 %s%%;text-align:center;font-size:7px;border:0.5px solid #555;box-sizing:border-box;line-height:%spx;%s">%s</span>' % (100 / per, r[3] / rows - 1, 'background:#ffb532;color:#111' if i == int(val or 0) else 'color:#ddd', html.escape(t)) for i, t in enumerate(e))
        div(r, 'display:flex;flex-wrap:wrap;overflow:hidden;background:#2b2b2b;border:1px solid #6c6c6c;zoom:%s' % (x.get('fontsize', 8) / 8.0 if False else 1), cells)
    elif mc == 'live.menu':
        e = v.get('parameter_enum', []); t = e[int(val or 0)] if e else ''
        div(r, 'background:#2b2b2b;border:1px solid #6c6c6c;color:#ddd;font-size:7px;padding-left:2px;line-height:%spx' % (r[3] - 2), html.escape(t) + '<span style="position:absolute;right:2px">▾</span>')
    elif mc == 'live.text':
        txt = x.get('text', '')
        if x.get('bordercolor') == [0.0, 0.0, 0.0, 0.0]: continue        # transparent: a drawn die shows
        if x.get('appearance') == 1:
            div(r, 'color:#ddd;font-size:7px;line-height:%spx;padding-left:12px' % r[3], '<span style="position:absolute;left:0;top:1px;width:9px;height:9px;border:1px solid #6c6c6c;background:%s"></span>%s' % ('#ffb532' if val else '#2b2b2b', html.escape(txt)))
        else:
            div(r, 'background:#2b2b2b;border:1px solid #6c6c6c;color:#ddd;font-size:7px;text-align:center;line-height:%spx' % (r[3] - 2), html.escape(txt))
    elif mc == 'live.dial':
        cx, cy = r[0] + r[2] / 2, r[1] + 13
        o.append('<svg style="position:absolute;left:0;top:0;overflow:visible" width="1" height="1"><circle cx="%s" cy="%s" r="10" fill="none" stroke="#1d1d1d" stroke-width="2"/><path d="M%s %s A10 10 0 0 1 %s %s" stroke="#ffb532" stroke-width="2" fill="none"/></svg>' % (cx, cy, cx - 7, cy + 7, cx, cy - 10))
        div([r[0], r[1] + r[3] - 13, r[2], 10], 'text-align:center;color:#ddd;font-size:7px', '0 %' if v.get('parameter_unitstyle') == 5 else '0 st')
    elif mc == 'live.numbox':
        if x.get('textcolor') == [0.0, 0.0, 0.0, 0.0]: continue        # transparent: the v8ui draws it
        div(r, 'background:#2b2b2b;border:1px solid #6c6c6c;color:#ddd;font-size:7px;text-align:center;line-height:%spx' % (r[3] - 2), str(val))
    elif mc == 'v8ui':
        kind, lo, hi = x['jsarguments'][:3]
        if kind == 'len':
            n = re.match(r'Stage(\d) ', x['varname']).group(1)
            byname = {y.get('varname'): y for y in B.values()}
            gv = lambda nm: int((init[byname[nm]['id']][0]) or 0)
            mode, dv, ln = gv('Stage%s Mode' % n), gv('Stage%s Div' % n) + 1, gv('Stage%s Length' % n) + 1
            notes = [] if mode == 0 else ([-1] if mode == 3 else list(range(0, ln, ln if mode == 1 else dv)))
            cw = (r[2] - 7) / 8.0
            for k in range(8):
                col = '#2b2b2b' if k >= ln else ('#e6e6e6' if (k in notes) else '#6c6c6c')
                div_ = [r[0] + k * (cw + 1), r[1], cw, r[3]]
                o.append('<div style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;background:%s"></div>' % (div_[0], div_[1], div_[2], div_[3], col))
            if notes == [-1]:
                o.append('<div style="position:absolute;left:%spx;top:%spx;width:%spx;height:%spx;background:#e6e6e6"></div>' % (r[0], r[1], ln * (cw + 1) - 1, r[3]))
            continue
        if kind == 'dice':
            pips = ''.join('<circle cx="%s" cy="%s" r="1.1" fill="#ddd"/>' % (r[0] + 1 + 11 * a, r[1] + 1 + 11 * b) for a, b in ((.28,.28),(.72,.28),(.5,.5),(.28,.72),(.72,.72)))
            o.append('<svg style="position:absolute;left:0;top:0;overflow:visible" width="1" height="1"><rect x="%s" y="%s" width="10" height="10" rx="2.5" fill="none" stroke="#ddd" stroke-width="1"/>%s</svg>' % (r[0] + 1.5, r[1] + 1.5, pips))
            continue
        feeds = {l['patchline']['source'][0] for l in P['lines'] if l['patchline']['destination'][0] == x['id']}
        nb = [y for y in B.values() if y['maxclass'] == 'live.numbox' and y['id'] in feeds]
        valn = init[nb[0]['id']][0] if nb else lo
        frac = 0 if kind == 'mode' else (max(0, min(1, (valn - lo) / float(hi - lo))) if hi > lo else 0)
        t = (NN[int(valn) % 12] + str(int(valn) // 12 - 2)) if kind == 'note' else (('%+d st' % valn) if kind == 'st' else (('%.2f %%' % valn) if kind == 'pct' else (('%d %%' % valn) if kind == 'ipct' else (('%d ms' % valn) if kind == 'ms' else (['rest', '1 note', 'hold', 'every 4', 'every 3', 'every 2', 'all'][int(valn)] if kind == 'mode' else str(int(valn)))))))
        div(r, 'background:#2b2b2b;border:1px solid #6c6c6c;overflow:hidden',
            '<div style="position:absolute;left:0;top:0;bottom:0;width:%s%%;background:#ffb532"></div><div style="position:absolute;inset:0;text-align:center;font-size:7px;line-height:%spx;color:#ddd;mix-blend-mode:difference">%s</div>' % (frac * 100, r[3] - 2, t))
o.append('</div></body></html>')
open(out, 'w').write('\n'.join(o))
