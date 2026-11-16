"""Build "XL-185 Sequencer.amxd" from the original ML-185 device (Nico Starke, 2009).

Usage: python3 -I build_ml185.py <original.amxd> <ml185scale.js> <out.amxd>

The original frozen device is read, its patcher is edited (new controls, the note path, the mod lanes,
the face) and the three scripts next to this file are embedded:
  ml185scale.js   scales, random tools, Mutate, Auto, undo, Slices, page groups
  ml185stage.js   per-stage tables, lane values, the look of stages that do not play
  ml185box.js     the drawn controls
"""
import copy
import json
import os
import re
import struct
import sys
import time


# ---- frozen container (.amxd = ampf/meta/ptch chunks, ptch = mx@c) ----------

def read_chunks(raw):
    chunks, i = [], 0
    while i < len(raw):
        tag = raw[i:i + 4]
        ln = struct.unpack('<I', raw[i + 4:i + 8])[0]
        chunks.append([tag, raw[i + 8:i + 8 + ln]])
        i += 8 + ln
    assert i == len(raw), 'trailing bytes in amxd'
    return chunks


def write_chunks(chunks):
    return b''.join(tag + struct.pack('<I', len(body)) + body for tag, body in chunks)


def parse_mxc(body):
    assert body[:4] == b'mx@c'
    hdr, zero, dirofs = struct.unpack('>III', body[4:16])
    assert hdr == 16 and zero == 0
    d = body[dirofs:]
    assert d[:4] == b'dlst'
    dlen = struct.unpack('>I', d[4:8])[0]
    assert dirofs + dlen == len(body), 'directory does not end the container'
    files, j = [], 8
    while j < dlen:
        assert d[j:j + 4] == b'dire'
        elen = struct.unpack('>I', d[j + 4:j + 8])[0]
        fields, k = {}, j + 8
        while k < j + elen:
            ftag = d[k:k + 4].decode()
            flen = struct.unpack('>I', d[k + 4:k + 8])[0]
            fields[ftag] = d[k + 8:k + flen]
            k += flen
        sz = struct.unpack('>I', fields['sz32'])[0]
        of = struct.unpack('>I', fields['of32'])[0]
        files.append({
            'type': fields['type'], 'name': fields['fnam'].rstrip(b'\0').decode(),
            'data': body[of:of + sz], 'vers': fields['vers'], 'flag': fields['flag'],
            'mdat': fields['mdat'],
        })
        j += elen
    return files


def field(tag, val):
    return tag + struct.pack('>I', 8 + len(val)) + val


def pad4(b):
    return b + b'\0' * ((4 - len(b) % 4) % 4)


def build_mxc(files):
    data, offs, pos = b'', [], 16
    for f in files:
        offs.append(pos)
        data += f['data']
        pos += len(f['data'])
    dirs = b''
    for f, of in zip(files, offs):
        e = (field(b'type', f['type']) + field(b'fnam', pad4(f['name'].encode() + b'\0'))
             + field(b'sz32', struct.pack('>I', len(f['data']))) + field(b'of32', struct.pack('>I', of))
             + field(b'vers', f['vers']) + field(b'flag', f['flag']) + field(b'mdat', f['mdat']))
        dirs += b'dire' + struct.pack('>I', 8 + len(e)) + e
    dlst = b'dlst' + struct.pack('>I', 8 + len(dirs)) + dirs
    return b'mx@c' + struct.pack('>III', 16, 0, 16 + len(data)) + data + dlst


def hfs_now():
    return struct.pack('>I', int(time.time()) + 2082844800)


# ---- patch helpers ---------------------------------------------------------

def boxes(p):
    return {b['box']['id']: b['box'] for b in p['boxes']}


def add_box(p, box):
    assert box['id'] not in boxes(p), box['id']
    p['boxes'].append({'box': box})
    return box['id']


def add_line(p, src, so, dst, di):
    p['lines'].append({'patchline': {'source': [src, so], 'destination': [dst, di],
                                     'hidden': 0, 'midpoints': []}})


def remove_line(p, src, so, dst, di):
    before = len(p['lines'])
    p['lines'] = [l for l in p['lines'] if not (
        l['patchline']['source'] == [src, so] and l['patchline']['destination'] == [dst, di])]
    assert len(p['lines']) == before - 1, 'line not found %s:%d -> %s:%d' % (src, so, dst, di)


def remove_box(p, bid):
    assert bid in boxes(p), bid
    p['boxes'] = [b for b in p['boxes'] if b['box']['id'] != bid]
    p['lines'] = [l for l in p['lines'] if l['patchline']['source'][0] != bid
                  and l['patchline']['destination'][0] != bid]


def newobj(bid, text, ins, outs, outtypes, rect):
    return {'maxclass': 'newobj', 'text': text, 'id': bid, 'numinlets': ins, 'numoutlets': outs,
            'outlettype': outtypes, 'patching_rect': rect, 'fontname': 'Arial', 'fontsize': 10.0}


def message(bid, text, rect):
    return {'maxclass': 'message', 'text': text, 'id': bid, 'numinlets': 2, 'numoutlets': 1,
            'outlettype': [''], 'patching_rect': rect, 'fontname': 'Arial', 'fontsize': 10.0}


def clone(template, bid, rect, prect, **attrs):
    b = copy.deepcopy(template)
    b['id'] = bid
    b['patching_rect'] = rect
    if prect is not None:
        b['presentation'] = 1
        b['presentation_rect'] = prect
    b.update(attrs)
    return b


def param(box, **v):
    val = box['saved_attribute_attributes']['valueof']
    for k in ('parameter_initial', 'parameter_enum'):
        val.pop(k, None)
    val.update(v)


def scale_names(js_src):
    block = js_src[js_src.index('var SCALES = ['):]
    block = block[:block.index('];')]
    return re.findall(r'^\s*\["([^"]+)"', block, re.M)


def live_names(js_src):
    block = js_src[js_src.index('var LIVE_NAMES = ['):]
    block = block[:block.index('];')]
    return re.findall(r'"([^"]+)"', block)


# ---- edits -----------------------------------------------------------------

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
# The drawn parts (ml185box.js) are jsui, the technique Ableton's own devices use on both platforms.
# Theme colours come in from a live.colors object (theme_feed), as in Ableton's jsui devices.
DRAW_CLASS = 'jsui'
THEME_NAMES = ['control_text_bg', 'contrast_frame', 'control_fg', 'control_selection', 'control_fg_on',
               'control_bg', 'control_fg_zombie',       # live.colors names (no "live_" prefix) the drawings use
               'control_zombie', 'control_fg_on_zombie']    # the inactive pair, for stages beyond Length
PITCH = ['obj-1', 'obj-2', 'obj-3', 'obj-39', 'obj-55', 'obj-54', 'obj-53', 'obj-43']
VELO = ['obj-11', 'obj-10', 'obj-9', 'obj-8', 'obj-7', 'obj-6', 'obj-5', 'obj-4']
AUX = ['obj-19', 'obj-18', 'obj-17', 'obj-16', 'obj-15', 'obj-14', 'obj-13', 'obj-12']
STAGE_MODE = ['obj-34', 'obj-31', 'obj-10', 'obj-37', 'obj-45', 'obj-48', 'obj-51', 'obj-41']


def edit(doc, js_src):
    P = doc['patcher']
    R = boxes(P)
    DL = R['obj-296']['patcher']
    D = boxes(DL)

    # sanity: the objects this edit relies on are what we think they are
    assert R['obj-178']['text'] == '+ 0'
    assert R['obj-88']['text'].startswith('makenote')
    assert R['obj-2']['text'] == 'gate 2' and R['obj-236']['text'] == 'bendout'
    assert R['obj-157']['varname'] == 'Length Mode' and R['obj-73']['varname'] == 'Direction'
    assert R['obj-267']['varname'] == 'Clock Timing' and R['obj-72']['varname'] == 'Sequence Length'
    assert [R[i]['varname'] for i in STAGE_MODE] == ['Stage%d Mode' % n for n in range(1, 9)]
    assert D['obj-72']['text'] == 'gate 3' and D['obj-286']['text'] == 'route 1 2 3 4 5 6 7 8'
    assert [D[i]['varname'] for i in PITCH] == ['PitchDial%d' % n for n in range(1, 9)]
    assert [D[i]['varname'] for i in VELO] == ['VelocityDial%d' % n for n in range(1, 9)]
    assert [D[i]['varname'] for i in AUX] == ['AuxDial%d' % n for n in range(1, 9)]

    # 1. Reload fix ---------------------------------------------------------
    X, Y = 1150.0, 40.0
    add_box(P, newobj('obj-920', 'live.thisdevice', 1, 3, ['bang', 'int', 'int'], [X, Y, 95.0, 20.0]))
    add_box(P, newobj('obj-921', 'deferlow', 1, 1, [''], [X, Y + 30, 55.0, 20.0]))
    add_box(P, newobj('obj-922', 't b b b b b b', 1, 6, ['bang'] * 6, [X, Y + 60, 170.0, 20.0]))
    add_box(P, newobj('obj-923', 's ---refresh', 1, 0, [], [X, Y + 95, 75.0, 20.0]))
    add_box(P, message('obj-924', 'init', [X + 160, Y + 95, 32.0, 20.0]))
    add_line(P, 'obj-920', 0, 'obj-921', 0)
    add_line(P, 'obj-921', 0, 'obj-922', 0)
    add_line(P, 'obj-922', 5, 'obj-924', 0)    # start the Live API side first
    add_line(P, 'obj-922', 4, 'obj-267', 0)    # Clock Timing
    add_line(P, 'obj-922', 3, 'obj-73', 0)     # Direction
    for tab in STAGE_MODE:                     # Stage Mode -> extra1 (gate length) in the grid
        add_line(P, 'obj-922', 2, tab, 0)
    add_line(P, 'obj-922', 1, 'obj-72', 0)     # Sequence Length -> loop (Length Mode is a toggle: never bang it)
    add_line(P, 'obj-922', 0, 'obj-923', 0)    # all step dials

    add_box(DL, newobj('obj-962', 'r ---refresh', 0, 1, [''], [520.0, 300.0, 75.0, 20.0]))
    for d in PITCH + VELO + AUX:
        add_line(DL, 'obj-962', 0, d, 0)

    # 2. Random through the scale engine -------------------------------------
    for bid in ('obj-284', 'obj-290', 'obj-79', 'obj-71', 'obj-285'):   # uzi, t i b, random 72, - 36, pack
        remove_box(DL, bid)
    add_box(DL, newobj('obj-960', 's ---rndpitch', 1, 0, [], [520.0, 330.0, 80.0, 20.0]))
    add_box(DL, newobj('obj-961', 'r ---rndset', 0, 1, [''], [520.0, 360.0, 75.0, 20.0]))
    add_line(DL, 'obj-72', 0, 'obj-960', 0)
    add_line(DL, 'obj-961', 0, 'obj-286', 0)

    assert [D[i]['text'] for i in ('obj-77', 'obj-75', 'obj-78', 'obj-76', 'obj-65')] == \
        ['uzi 8', 't i b', 'random 127', 'pack', 'route 1 2 3 4 5 6 7 8']
    for bid in ('obj-77', 'obj-75', 'obj-78', 'obj-76'):   # velocity: uzi, t i b, random 127, pack
        remove_box(DL, bid)
    add_box(DL, newobj('obj-963', 's ---rndvel', 1, 0, [], [620.0, 330.0, 70.0, 20.0]))
    add_box(DL, newobj('obj-964', 'r ---rndvelset', 0, 1, [''], [620.0, 360.0, 90.0, 20.0]))
    add_line(DL, 'obj-72', 1, 'obj-963', 0)
    add_line(DL, 'obj-964', 0, 'obj-65', 0)

    # 3. Scale engine -------------------------------------------------------
    names = scale_names(js_src)
    assert len(names) >= 20 and names[2] == 'Minor', names
    X, Y = 1150.0, 220.0
    add_box(P, newobj('obj-930', 'js ml185scale.js', 1, 7, [''] * 7, [X, Y + 120, 300.0, 20.0]))
    add_line(P, 'obj-930', 6, 'obj-920', 0)     # setwidth: the settings column folds
    prepends = [('obj-931', 'source', 'obj-901'), ('obj-932', 'root', 'obj-902'),
                ('obj-933', 'scale', 'obj-903'), ('obj-934', 'bendrange', 'obj-904'),
                ('obj-935', 'lo', 'obj-905'), ('obj-936', 'hi', 'obj-906'),
                ('obj-927', 'vlo', 'obj-907'), ('obj-928', 'vhi', 'obj-908')]
    for n, (bid, word, _) in enumerate(prepends):
        add_box(P, newobj(bid, 'prepend ' + word, 1, 1, [''], [X + n * 95, Y + 60, 90.0, 20.0]))
    add_box(P, newobj('obj-937', 'r ---rndpitch', 0, 1, [''], [X + 570, Y + 30, 85.0, 20.0]))
    add_box(P, message('obj-938', 'random', [X + 570, Y + 60, 50.0, 20.0]))
    add_box(P, newobj('obj-939', 's ---rndset', 1, 0, [], [X + 100, Y + 160, 75.0, 20.0]))
    add_line(P, 'obj-924', 0, 'obj-930', 0)
    add_line(P, 'obj-937', 0, 'obj-938', 0)
    add_line(P, 'obj-938', 0, 'obj-930', 0)
    add_line(P, 'obj-930', 2, 'obj-939', 0)
    add_box(P, newobj('obj-929', 'r ---rndvel', 0, 1, [''], [X + 670, Y + 30, 75.0, 20.0]))
    add_box(P, message('obj-954', 'randomvel', [X + 670, Y + 60, 65.0, 20.0]))
    add_box(P, newobj('obj-955', 's ---rndvelset', 1, 0, [], [X + 200, Y + 160, 90.0, 20.0]))
    add_line(P, 'obj-929', 0, 'obj-954', 0)
    add_line(P, 'obj-954', 0, 'obj-930', 0)
    add_line(P, 'obj-930', 3, 'obj-955', 0)

    # UI: new column right of the existing one (x 596..700)
    tab, menu, numbox = R['obj-73'], R['obj-267'], R['obj-291']
    label, panel, vline = R['obj-126'], R['obj-120'], R['obj-8']
    UX, UY = 1150.0, 0.0

    b = clone(tab, 'obj-901', [UX, UY, 90.0, 14.0], [606.0, 19.0, 90.0, 14.0], varname='Scale Source',
              annotation='Where the scale comes from. OFF: notes play as set. LIVE: follow the Live set: '
                         'its scale (root and scale, while Scale Mode is on) and its Tuning System. '
                         'OWN: use the Root and Scale menus below; scales marked ~ are microtonal and '
                         'use pitch bend.')
    param(b, parameter_enum=['OFF', 'LIVE', 'OWN'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Scale Source', parameter_shortname='Source', parameter_mmin=0.0,
          parameter_mmax=2.0, parameter_invisible=0)
    add_box(P, b)

    # Clock Timing (and the new menus cloned from it) forced a light background: with
    # Live's theme text colour that reads white on white. Let the theme colour them.
    R['obj-267'].pop('activebgcolor', None)
    menu = copy.deepcopy(R['obj-267'])
    b = clone(menu, 'obj-902', [UX + 100, UY, 40.0, 15.0], [606.0, 48.0, 28.0, 13.0], varname='Scale Root',
              annotation='Root note for the device\'s own scales.')
    param(b, parameter_enum=NOTE_NAMES, parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Scale Root', parameter_shortname='Root', parameter_mmin=0.0,
          parameter_mmax=11.0, parameter_invisible=0)
    add_box(P, b)

    b = clone(menu, 'obj-903', [UX + 150, UY, 100.0, 15.0], [637.0, 48.0, 59.0, 13.0], varname='Scale',
              annotation='Scale used when Source is OWN. Notes snap to the nearest scale note. Scales '
                         'marked ~ are microtonal: they play the nearest semitone plus pitch bend, so set '
                         'Bend Range to match your synth.')
    param(b, parameter_enum=names, parameter_initial=[2], parameter_initial_enable=1,
          parameter_longname='Scale', parameter_shortname='Scale', parameter_mmin=0.0,
          parameter_mmax=float(len(names) - 1), parameter_invisible=0)
    add_box(P, b)

    # LIVE mode display: same place as Root/Scale, greyed and unclickable, shows the project's
    # scale. Hidden parameters (not saved, not automatable, no undo), swapped in by the script.
    lnames = live_names(js_src)
    assert 'Minor' in lnames and lnames[-3:] == ['Scale off', 'Live tuning', '(other)'], lnames
    for bid, var, enum, prect in (('obj-909', 'Live Root Display', NOTE_NAMES, [606.0, 48.0, 28.0, 13.0]),
                                  ('obj-919', 'Live Scale Display', lnames, [637.0, 48.0, 59.0, 13.0])):
        b = clone(menu, bid, [UX + 100 + (prect[0] - 606.0), UY + 30, prect[2], 15.0], prect, varname=var,
                  annotation='Scale of the Live set (Source is LIVE). Change it in Live\'s Scale settings.',
                  hidden=1, ignoreclick=1)
        param(b, parameter_enum=enum, parameter_initial_enable=0, parameter_longname=var,
              parameter_shortname=var.replace(' Display', ''), parameter_mmin=0.0,
              parameter_mmax=float(len(enum) - 1), parameter_invisible=2)
        add_box(P, b)

    b = clone(numbox, 'obj-904', [UX + 260, UY, 51.0, 13.0], [606.0, 76.0, 51.0, 13.0], varname='Bend Range',
              annotation='Pitch bend range of the synth you play, in semitones. Only used by microtonal '
                         'scales (marked ~). Set the synth to the same range.')
    param(b, parameter_initial=[2], parameter_initial_enable=1, parameter_type=1, parameter_unitstyle=7,
          parameter_mmin=1.0, parameter_mmax=24.0, parameter_steps=0, parameter_longname='Pitch Bend Range',
          parameter_shortname='Bend', parameter_invisible=1)
    add_box(P, b)

    for bid, var, short, init, px, text in (
            ('obj-905', 'Note Range Low', 'Note Lo', 36, 606.0, 'Lowest note Global, Mutate and the Pitch die pick.'),
            ('obj-906', 'Note Range High', 'Note Hi', 60, 653.0, 'Highest note Global, Mutate and the Pitch die pick.')):
        b = clone(numbox, bid, [UX + 320 + (px - 606.0), UY, 43.0, 13.0], [px, 123.0, 43.0, 13.0],
                  varname=var, annotation=text + ' With a scale on, only scale notes are picked. '
                  'With a Live tuning loaded, the range is matched by pitch.')
        param(b, parameter_initial=[init], parameter_initial_enable=1, parameter_type=1, parameter_unitstyle=8,
              parameter_mmin=24.0, parameter_mmax=96.0, parameter_steps=0, parameter_longname=var,
              parameter_shortname=short, parameter_invisible=1)
        add_box(P, b)
    for bid, var, short, init, px, text in (
            ('obj-907', 'Velocity Range Low', 'Vel Lo', 1, 606.0, 'Lowest velocity Global, Mutate and the Vel die pick.'),
            ('obj-908', 'Velocity Range High', 'Vel Hi', 127, 653.0, 'Highest velocity Global, Mutate and the Vel die pick.')):
        b = clone(numbox, bid, [UX + 420 + (px - 606.0), UY, 43.0, 13.0], [px, 148.0, 43.0, 13.0],
                  varname=var, annotation=text + ' Velocity 0 is a note-off (a silent step).')
        param(b, parameter_initial=[init], parameter_initial_enable=1, parameter_type=1, parameter_unitstyle=0,
              parameter_mmin=0.0, parameter_mmax=127.0, parameter_steps=0, parameter_longname=var,
              parameter_shortname=short, parameter_invisible=1)
        add_box(P, b)

    # Value boxes drawn like the stage tabs: Live's "Control On" orange fill, text light over the
    # background and black where the fill covers it. live.numbox can only have one text colour
    # (and Live's theme overrides a static one), so a click-through drawing (jsui, ml185box.js) draws the
    # box; the number box keeps the mouse, typing, mapping and saving but is made fully
    # transparent. Live draws its number boxes above the drawing, so it shows through.
    # Colours are marked fixed ({"expression": ""}), otherwise Live's theme overrides them.
    assert R['obj-291']['varname'] == 'Swing Amount'
    overlays = (('obj-291', 'obj-970', 'Swing Display', 'pct', 0, 100),      # obj-291 = original Swing
                ('obj-904', 'obj-971', 'Bend Range Display', 'st', 1, 24),
                ('obj-905', 'obj-972', 'Note Range Low Display', 'note', 24, 96),
                ('obj-906', 'obj-973', 'Note Range High Display', 'note', 24, 96),
                ('obj-907', 'obj-974', 'Velocity Range Low Display', 'int', 0, 127),
                ('obj-908', 'obj-975', 'Velocity Range High Display', 'int', 0, 127))
    for nid, vid, var, kind, lo, hi in overlays:
        x = boxes(P)[nid]
        v = x['saved_attribute_attributes']['valueof']
        assert (v['parameter_mmin'], v['parameter_mmax']) == (lo, hi), (nid, v['parameter_mmin'], v['parameter_mmax'])
        for attr in ('activebgcolor', 'activeslidercolor', 'textcolor', 'bordercolor', 'focusbordercolor'):
            x[attr] = [0.0, 0.0, 0.0, 0.0]
            x['saved_attribute_attributes'][attr] = {'expression': ''}
        pr = x['presentation_rect']
        add_box(P, {'maxclass': DRAW_CLASS, 'id': vid, 'varname': var, 'filename': 'ml185box.js',
                    'jsarguments': [kind, lo, hi], 'numinlets': 1, 'numoutlets': 0, 'outlettype': [],
                    'parameter_enable': 0, 'border': 0, 'ignoreclick': 1,
                    'patching_rect': [UX + 700, UY + 20 * (int(vid[-2:]) - 70), pr[2], pr[3]],
                    'presentation': 1, 'presentation_rect': list(pr), 'hidden': x.get('hidden', 0),
                    'annotation': x.get('annotation', '')})
        add_line(P, nid, 0, vid, 0)
        add_line(P, 'obj-922', 0, nid, 0)    # after load: re-send the value so the drawing is current

    # Random stays in the stage header (original place and look) and randomizes the page shown.
    # A button-mode live.text sends a bang (the original fed it straight into uzi 8). Accept the
    # bang, and a 1 in case a Live version sends values, but never a 0 (release).
    rnd = R['obj-17']
    assert rnd['varname'] == 'Random' and rnd['presentation_rect'] == [477.0, 3.0, 37.0, 11.0]
    rnd['annotation'] = ('Randomizes the page shown (Pitch, Vel or Aux). Pitch picks from Note Range (scale '
                         'notes only when a scale is on), Vel from Velocity Range, Aux from 0-127.')
    add_box(P, newobj('obj-997', 'route bang', 2, 2, ['', ''], [470.0, 30.0, 60.0, 20.0]))
    add_box(P, newobj('obj-996', 'sel 1', 2, 2, ['bang', ''], [540.0, 55.0, 40.0, 20.0]))
    remove_line(P, 'obj-17', 0, 'obj-1', 0)
    add_line(P, 'obj-17', 0, 'obj-997', 0)
    add_line(P, 'obj-997', 0, 'obj-1', 0)      # bang
    add_line(P, 'obj-997', 1, 'obj-996', 0)    # numbers: only 1
    add_line(P, 'obj-996', 0, 'obj-1', 0)

    # RANDOM ALL column (x 708..800): the button, then what it includes
    CX = 708.0
    b = clone(rnd, 'obj-980', [UX + 760, UY, 92.0, 14.0], [CX, 19.0, 92.0, 14.0], varname='Random All',
              text='RANDOM ALL',
              annotation='Randomizes everything ticked below in one go: Pitch (from Note Range; scale notes only '
                         'when a scale is on), Velocity (from Velocity Range), and for all 8 stages St. Mode, '
                         'St. Div and St. Length, plus Seq Length (the Length knob).')
    b.pop('bordercolor', None)        # Live's normal button border
    b.pop('focusbordercolor', None)
    param(b, parameter_enum=['val1', 'val2'], parameter_longname='Random All', parameter_shortname='Rnd All',
          parameter_invisible=2)
    add_box(P, b)
    add_box(P, newobj('obj-998', 'route bang', 2, 2, ['', ''], [UX + 760, UY + 20, 60.0, 20.0]))
    add_box(P, newobj('obj-987', 'sel 1', 2, 2, ['bang', ''], [UX + 830, UY + 40, 40.0, 20.0]))
    add_box(P, message('obj-988', 'randomall', [UX + 760, UY + 60, 65.0, 20.0]))
    add_line(P, 'obj-980', 0, 'obj-998', 0)
    add_line(P, 'obj-998', 0, 'obj-988', 0)
    add_line(P, 'obj-998', 1, 'obj-987', 0)
    add_line(P, 'obj-987', 0, 'obj-988', 0)
    add_line(P, 'obj-988', 0, 'obj-930', 0)

    # (param name, face label, short, default, what, prepend id); the param names stay (sets restore by
    # name), the face says the sequencer's own words; Prob and Slide added then
    rnd_flags = (('Pitch', 'Pitch', 'Pitch', 1, 'the 8 pitch knobs (from Note range; scale notes only when a scale is on)', 'obj-989'),
                 ('Velocity', 'Velocity', 'Vel', 1, 'the 8 velocities (from Velocity range)', 'obj-990'),
                 ('St. Mode', 'Mode', 'Mode', 0, 'each stage\'s Mode (rest, once, hold, repeats)', 'obj-991'),
                 ('St. Div', 'Div', 'Div', 0, 'each stage\'s repeat interval (for the repeating modes)', 'obj-992'),
                 ('St. Length', 'Len', 'StLen', 0, 'each stage\'s length (1-8 clock steps)', 'obj-993'),
                 ('Seq Length', 'Seq Length', 'SeqLen', 0, 'the sequence Length (how many stages play, 1-8)', 'obj-994'),
                 ('Probability', 'Prob', 'Prob', 0, 'each stage\'s Probability (Global picks 25, 50, 75 or 100 %; '
                  'Reset: 100 %)', 'obj-4492'),
                 ('Slide', 'Slide', 'Slide', 0, 'each stage\'s Slide (Global turns it on for about one stage in '
                  'four; Reset: off)', 'obj-4493'))
    flag_ids = ['obj-981', 'obj-982', 'obj-983', 'obj-984', 'obj-985', 'obj-986', 'obj-4490', 'obj-4491']
    for n, (text, face, short, init, what, pre) in enumerate(rnd_flags):   # order = RND_KEYS in ml185scale.js
        bid = flag_ids[n]
        b = clone(rnd, bid, [UX + 760, UY + 100 + 20 * n, 92.0, 12.0], [CX, 40.0 + 14.0 * n, 92.0, 12.0],
                  varname='Random All ' + text, text=face, texton=face, mode=1, appearance=1,
                  annotation='Tick to let Global (and Mutate) change ' + what + '.')
        for k in ('bordercolor', 'focusbordercolor', 'transition'):
            b.pop(k, None)
        param(b, parameter_enum=['off', 'on'], parameter_initial=[init], parameter_initial_enable=1,
              parameter_longname='Random All ' + text, parameter_shortname='RA ' + short,
              parameter_mmin=0.0, parameter_mmax=1.0, parameter_invisible=1)
        add_box(P, b)
        add_box(P, newobj(pre, 'prepend rndflag %d' % n, 1, 1, [''], [UX + 860, UY + 100 + 20 * n, 110.0, 20.0]))
        add_line(P, bid, 0, pre, 0)
        add_line(P, pre, 0, 'obj-930', 0)

    for bid, text, prect in (
            ('obj-911', 'Scale Source', [604.0, 33.0, 70.0, 16.0]),
            ('obj-912', 'Root', [604.0, 61.0, 30.0, 16.0]),
            ('obj-913', 'Scale', [635.0, 61.0, 40.0, 16.0]),
            ('obj-914', 'Bend Range', [604.0, 89.0, 60.0, 16.0]),
            ('obj-915', 'Note Range', [604.0, 133.0, 94.0, 16.0]),
            ('obj-926', 'Velocity Range', [604.0, 157.0, 94.0, 16.0])):
        add_box(P, clone(label, bid, [UX + 420, UY + 20 * (int(bid[-2:]) - 10), prect[2], 16.0], prect,
                         text=text, ignoreclick=1))
    # Bend Range + label start hidden; ml185scale.js shows them only for microtonal scales
    boxes(P)['obj-914']['varname'] = 'Bend Range Label'
    boxes(P)['obj-914']['hidden'] = 1
    boxes(P)['obj-904']['hidden'] = 1
    boxes(P)['obj-971']['hidden'] = 1     # its drawn display
    add_box(P, clone(panel, 'obj-917', [UX + 520, UY, 98.0, 68.0], [602.0, 103.0, 98.0, 68.0],
                     background=1, ignoreclick=1))
    add_box(P, clone(vline, 'obj-918', [UX + 630, UY, 5.0, 155.0], [596.0, 18.0, 5.0, 155.0]))
    add_box(P, clone(vline, 'obj-995', [UX + 640, UY, 5.0, 155.0], [702.0, 18.0, 5.0, 155.0]))   # before RANDOM ALL
    # panels belong behind the controls: move the new panel to the front of the box list
    P['boxes'].insert(0, P['boxes'].pop(next(i for i, b in enumerate(P['boxes']) if b['box']['id'] == 'obj-917')))

    for bid, word, ctl in prepends:
        add_line(P, ctl, 0, bid, 0)
        add_line(P, bid, 0, 'obj-930', 0)

    # note path: + coarse -> clip -> [bend first] -> note lookup -> makenote
    X, Y = 1150.0, 560.0
    add_box(P, newobj('obj-940', 'clip 0 127', 3, 1, ['int'], [X, Y, 65.0, 20.0]))
    add_box(P, newobj('obj-941', 't i i', 1, 2, ['int', 'int'], [X, Y + 30, 40.0, 20.0]))
    add_box(P, newobj('obj-942', 'zl.lookup', 2, 2, ['', ''], [X, Y + 120, 60.0, 20.0]))
    add_box(P, newobj('obj-943', 'gate 1', 2, 1, [''], [X + 100, Y + 60, 45.0, 20.0]))
    add_box(P, newobj('obj-944', 'zl.lookup', 2, 2, ['', ''], [X + 100, Y + 90, 60.0, 20.0]))
    add_box(P, newobj('obj-945', 't b i i', 1, 3, ['bang', 'int', 'int'], [X + 100, Y + 120, 55.0, 20.0]))
    add_box(P, newobj('obj-946', '& 127', 2, 1, ['int'], [X + 140, Y + 150, 45.0, 20.0]))
    add_box(P, newobj('obj-947', '>> 7', 2, 1, ['int'], [X + 190, Y + 150, 40.0, 20.0]))
    add_box(P, newobj('obj-948', 'pack 224 0 0', 3, 1, [''], [X + 100, Y + 180, 80.0, 20.0]))
    add_box(P, newobj('obj-949', 'midiout', 1, 0, [], [X + 100, Y + 210, 50.0, 20.0]))
    remove_line(P, 'obj-178', 0, 'obj-88', 0)
    add_line(P, 'obj-178', 0, 'obj-940', 0)
    add_line(P, 'obj-940', 0, 'obj-941', 0)
    add_line(P, 'obj-941', 1, 'obj-943', 1)   # right outlet fires first: bend before note
    add_line(P, 'obj-943', 0, 'obj-944', 0)
    add_line(P, 'obj-944', 0, 'obj-945', 0)
    add_line(P, 'obj-945', 2, 'obj-947', 0)
    add_line(P, 'obj-947', 0, 'obj-948', 2)
    add_line(P, 'obj-945', 1, 'obj-946', 0)
    add_line(P, 'obj-946', 0, 'obj-948', 1)
    add_line(P, 'obj-945', 0, 'obj-948', 0)
    add_line(P, 'obj-948', 0, 'obj-949', 0)
    add_line(P, 'obj-941', 0, 'obj-942', 0)
    add_line(P, 'obj-942', 0, 'obj-88', 0)
    # fallback: notes pass straight through until the script sends its table
    add_box(P, newobj('obj-952', 'loadbang', 1, 1, ['bang'], [X - 120, Y + 60, 60.0, 20.0]))
    add_box(P, message('obj-953', ' '.join(str(i) for i in range(128)), [X - 120, Y + 90, 100.0, 20.0]))
    add_line(P, 'obj-952', 0, 'obj-953', 0)
    add_line(P, 'obj-953', 0, 'obj-942', 1)
    add_line(P, 'obj-930', 0, 'obj-942', 1)
    add_line(P, 'obj-930', 1, 'obj-944', 1)
    add_line(P, 'obj-930', 4, 'obj-943', 0)
    add_line(P, 'obj-930', 5, 'obj-949', 0)

    # Aux layer pitch bend is muted while a microtonal scale owns the bend
    add_box(P, newobj('obj-950', '!- 1', 2, 1, ['int'], [X + 260, Y + 60, 35.0, 20.0]))
    add_box(P, newobj('obj-951', 'gate 1 1', 2, 1, [''], [X + 260, Y + 90, 55.0, 20.0]))
    remove_line(P, 'obj-2', 0, 'obj-236', 0)
    add_line(P, 'obj-2', 0, 'obj-951', 1)
    add_line(P, 'obj-951', 0, 'obj-236', 0)
    add_line(P, 'obj-930', 4, 'obj-950', 0)
    add_line(P, 'obj-950', 0, 'obj-951', 0)

    # 4. Playback fixes from the bug audit ------------------------------------
    T = R['obj-294']['patcher']                     # p Transportation
    TB = boxes(T)
    assert TB['obj-28']['text'] == 'transport' and TB['obj-28']['numoutlets'] == 9
    SW = TB['obj-21']['patcher']                    # p swinger
    assert boxes(SW)['obj-16']['text'] == 'change -1'
    LED = R['obj-76']['patcher']                    # p LEDdriver
    pipes = sorted(i for i, x in boxes(LED).items() if x.get('text') == 'pipe 32n')
    assert len(pipes) == 8
    assert R['obj-251']['varname'] == 'live.step[2]' and R['obj-162']['text'] == 'change'
    assert R['obj-151']['text'] == 's ---chtrig' and R['obj-127']['text'] == 'if $i1==2 then 3 else $i1'

    # (a) Restart: a jump in song position (Play from the start, a loop wrap, relocating) resets
    # the tick grid, the stage-change detector, the stage counters and the swing state, so the
    # first stage enters exactly like a normal stage change. Continue-play is not a jump.
    # A poll that comes late in Live sees the position more than 40 ticks ahead too; that used to
    # count as a jump and restart the playing stage mid-visit. A forward gap is a jump only when it is also
    # longer than one clock step at the current Rate (60 / 120 / 240 / 480 ticks, from ---userQuant): the
    # grid takes its step from the song position, so after a shorter gap it lands on the right step by
    # itself. Backward moves stay jumps. Until Rate arrives the step is 0, which leaves the plain 40-tick rule.
    assert not any(k in boxes(T) for k in ('obj-904', 'obj-905'))
    add_box(T, newobj('obj-900', 't f f', 1, 2, ['float', 'float'], [600.0, 300.0, 40.0, 20.0]))
    add_box(T, newobj('obj-901', '- 0.', 2, 1, ['float'], [600.0, 330.0, 40.0, 20.0]))
    add_box(T, newobj('obj-902', 'if $f1 < 0. || ($f1 > 40. && $f1 > $f2) then bang', 2, 1, [''],
                      [600.0, 360.0, 260.0, 20.0]))
    add_box(T, newobj('obj-903', 's ---xreset', 1, 0, [], [600.0, 390.0, 75.0, 20.0]))
    add_box(T, newobj('obj-904', 'r ---userQuant', 0, 1, [''], [800.0, 300.0, 90.0, 20.0]))   # Rate index 0..3
    add_box(T, newobj('obj-905', 'expr 60 << $i1', 1, 1, [''], [800.0, 330.0, 90.0, 20.0]))  # one step in ticks
    add_line(T, 'obj-28', 7, 'obj-900', 0)      # raw ticks, fires before this poll's time message
    add_line(T, 'obj-900', 1, 'obj-901', 0)     # new - previous
    add_line(T, 'obj-900', 0, 'obj-901', 1)     # then keep as previous
    add_line(T, 'obj-901', 0, 'obj-902', 0)     # polls are 10 ticks apart; < 0, or > 40 and > a step = a jump
    add_line(T, 'obj-902', 0, 'obj-903', 0)
    add_line(T, 'obj-904', 0, 'obj-905', 0)
    add_line(T, 'obj-905', 0, 'obj-902', 1)
    add_box(SW, newobj('obj-900', 'r ---xreset', 0, 1, [''], [400.0, 20.0, 75.0, 20.0]))
    add_box(SW, message('obj-901', 'set -1', [400.0, 50.0, 45.0, 20.0]))
    add_line(SW, 'obj-900', 0, 'obj-901', 0)
    add_line(SW, 'obj-901', 0, 'obj-16', 0)
    X, Y = 1150.0, 900.0
    add_box(P, newobj('obj-1001', 'r ---xreset', 0, 1, [''], [X, Y, 75.0, 20.0]))
    add_box(P, newobj('obj-1002', 't b b b', 1, 3, ['bang'] * 3, [X, Y + 30, 60.0, 20.0]))
    add_box(P, message('obj-1003', 'reset', [X + 120, Y + 60, 40.0, 20.0]))
    add_box(P, message('obj-1004', 'set 0', [X + 60, Y + 60, 40.0, 20.0]))
    add_box(P, message('obj-1005', '0', [X, Y + 60, 30.0, 20.0]))
    add_line(P, 'obj-1001', 0, 'obj-1002', 0)
    add_line(P, 'obj-1002', 2, 'obj-1003', 0)
    add_line(P, 'obj-1003', 0, 'obj-251', 0)    # tick grid: forget the current step
    add_line(P, 'obj-1002', 1, 'obj-1004', 0)
    add_line(P, 'obj-1004', 0, 'obj-162', 0)    # next stage counts as a new stage
    add_line(P, 'obj-1002', 0, 'obj-1005', 0)
    add_line(P, 'obj-1005', 0, 'obj-151', 0)    # stage counters back to the start

    # (b) Stop: when Live's transport stops or the device is switched off, end every held note
    # (makenote's tick-timed note-offs pause with the transport) and turn the LEDs off.
    add_box(P, newobj('obj-1006', 'metro 20 @active 1', 2, 1, ['bang'], [X + 220, Y, 110.0, 20.0]))
    add_box(P, newobj('obj-1007', 'transport', 2, 9, list(TB['obj-28']['outlettype']), [X + 220, Y + 30, 60.0, 20.0]))
    add_box(P, newobj('obj-1008', 'change', 1, 3, ['', 'int', 'int'], [X + 220, Y + 60, 50.0, 20.0]))
    add_box(P, newobj('obj-1009', 'sel 0', 2, 2, ['bang', ''], [X + 220, Y + 90, 40.0, 20.0]))
    add_box(P, newobj('obj-1010', 'sel 0', 2, 2, ['bang', ''], [X + 300, Y + 90, 40.0, 20.0]))
    add_box(P, newobj('obj-1011', 's ---xstop', 1, 0, [], [X + 220, Y + 120, 70.0, 20.0]))
    add_box(P, newobj('obj-1012', 'r ---xstop', 0, 1, [''], [X + 360, Y + 30, 70.0, 20.0]))
    add_box(P, message('obj-1013', 'stop', [X + 360, Y + 60, 35.0, 20.0]))
    add_line(P, 'obj-1006', 0, 'obj-1007', 0)
    add_line(P, 'obj-1007', 6, 'obj-1008', 0)   # transport state
    add_line(P, 'obj-1008', 0, 'obj-1009', 0)
    add_line(P, 'obj-1009', 0, 'obj-1011', 0)
    add_line(P, 'obj-920', 1, 'obj-1010', 0)    # device switched off
    add_line(P, 'obj-1010', 0, 'obj-1011', 0)
    add_line(P, 'obj-1012', 0, 'obj-1013', 0)
    add_line(P, 'obj-1013', 0, 'obj-88', 0)     # makenote: note-offs for everything held
    add_box(LED, newobj('obj-900', 'r ---xstop', 0, 1, [''], [700.0, 20.0, 70.0, 20.0]))
    add_box(LED, message('obj-901', 'flush', [700.0, 50.0, 40.0, 20.0]))
    add_line(LED, 'obj-900', 0, 'obj-901', 0)

    # (c) LEDs at 1/32: the off-timer was one 1/32 note, the same as a pulse, so repeated hits
    # looked like one light. Half a pulse at 1/32, unchanged at 1/16 and 1/8.
    add_box(LED, newobj('obj-902', 'r ---userQuant', 0, 1, [''], [800.0, 20.0, 85.0, 20.0]))
    add_box(LED, newobj('obj-903', 'sel 0', 2, 2, ['bang', ''], [800.0, 50.0, 40.0, 20.0]))
    add_box(LED, message('obj-904', 'delaytime 64n', [800.0, 80.0, 80.0, 20.0]))
    add_box(LED, message('obj-905', 'delaytime 32n', [890.0, 80.0, 80.0, 20.0]))
    add_line(LED, 'obj-902', 0, 'obj-903', 0)
    add_line(LED, 'obj-903', 0, 'obj-904', 0)   # 1/32
    add_line(LED, 'obj-903', 1, 'obj-905', 0)   # 1/16, 1/8
    for pid in pipes:
        add_line(LED, 'obj-901', 0, pid, 0)
        add_line(LED, 'obj-904', 0, pid, 0)
        add_line(LED, 'obj-905', 0, pid, 0)

    # (d) Direction: live.step's "direction 2" is back and forth and 3 is Rotate. The original
    # mapped [><] to 3, so ping-pong played Rotate. [><] now sends 2 (tab index 2 as is).
    remove_box(P, 'obj-127')
    add_line(P, 'obj-86', 0, 'obj-150', 0)

    # The DialLayer bpatcher shows its editing view, so like every other helper there,
    # the new objects and cables must be hidden when locked.
    for b in DL['boxes']:
        if b['box']['id'] in ('obj-960', 'obj-961', 'obj-962', 'obj-963', 'obj-964'):
            b['box']['hidden'] = 1
    for l in DL['lines']:
        l['patchline']['hidden'] = 1

    # Info View texts (shown in Live when hovering a control): fill the gaps, update the ones
    # the new features changed, and keep the drawn boxes in step with their number boxes.
    pitch_ann = ('Pitch of this stage in semitones; 0 st = C3. With a scale on (Live or one of the own scales), '
                 'the note played snaps to the nearest scale note. The knob keeps its position, so turning '
                 'the scale off brings the original note back.')
    for d in PITCH:
        D[d]['annotation'] = pitch_ann
    for d in VELO:
        D[d]['annotation'] = 'Velocity of this stage (0-127). 0 makes the stage silent.'
    R['obj-18']['annotation'] += ' With a scale on, the transposed notes also snap to the scale.'
    R['obj-14']['annotation'] += ' Random randomizes the layer shown here.'
    R['obj-266']['annotation'] = 'Total length of the sequence in clock ticks (max. 64).'
    B = boxes(P)
    B['obj-901']['annotation'] = (
        'Where the scale comes from. OFF: notes play as set. LIVE: follow the Live set\'s scale while '
        'Live\'s Scale Mode is on; Root and Scale then show it, greyed. With a Tuning System loaded in Live, '
        'notes pass through and Live retunes them. OWN: use the Root and Scale menus. Scales marked ~ are '
        'microtonal and use pitch bend.')
    B['obj-903']['annotation'] = (
        'Scale for OWN mode. Notes snap to the nearest scale note; the knobs keep their positions, so '
        'switching back restores the melody. Scales marked ~ are microtonal: nearest semitone plus pitch '
        'bend, so set Bend Range to match your synth.')
    for bid in ('obj-909', 'obj-919'):
        B[bid]['annotation'] = (
            'The Live set\'s root (Scale is Live). Change it in Live\'s scale settings; while Live\'s Scale '
            'Mode is off, notes play as set.')
    B['obj-904']['annotation'] = (
        'Pitch bend range of your synth, in semitones. Microtonal scales (~) play the nearest semitone '
        'plus pitch bend, so this must match the synth\'s own bend range or the in-between notes come out '
        'sharp or flat.')
    for nid, vid, var, kind, lo, hi in overlays:
        B[vid]['annotation'] = B[nid]['annotation']

    # Rate 1/4 (left-menu revision): a fourth interval of 480 ticks; swingCalc.js plays it straight
    assert R['obj-261']['text'] == 'sel 0 1 2' and R['obj-263']['text'] == 'interval $1 ticks'
    R['obj-261'].update(text='sel 0 1 2 3', numoutlets=5, outlettype=['bang'] * 4 + [''])
    add_box(P, message('obj-4206', '480', [690.0, 175.0, 40.0, 18.0]))
    add_line(P, 'obj-261', 3, 'obj-4206', 0)
    add_line(P, 'obj-4206', 0, 'obj-263', 0)

    # the face, the note path, R direction, the mod lanes
    p1_layout(P)
    p2_engine(P)
    p3_engine(P)
    p5_engine(P)
    remove_aux_and_length_mode(P)
    fix_pass(P)
    mappable_buttons(P)
    info_texts(P)
    theme_feed(P)

    # parameters registry (rebuilt: controls moved between patchers) and device frame
    P['parameters'] = registry(P)
    P['parameters']['parameterbanks'] = push_banks()
    P['dependencies'].append({'name': 'ml185scale.js', 'type': 'TEXT'})
    P['dependencies'].append({'name': 'ml185box.js', 'type': 'TEXT'})
    P['dependencies'].append({'name': 'ml185stage.js', 'type': 'TEXT'})
    P['devicewidth'] = 786.0
    return doc


def registry(P):
    """Every parameter-enabled object, keyed like Max does: id, or bpatcher-id::id inside subpatchers."""
    reg = {}
    def walk(p, prefix):
        for b in p['boxes']:
            x = b['box']
            v = x.get('saved_attribute_attributes', {}).get('valueof')
            if x.get('parameter_enable') == 1 and v:
                reg[prefix + x['id']] = [v['parameter_longname'], v['parameter_shortname'], 0]
            if 'patcher' in x:
                walk(x['patcher'], prefix + x['id'] + '::')
    walk(P, '')
    return reg


def strip_colors(x):
    """Back to Live's theme colours: drop static colour overrides (keeps drawn-box transparency)."""
    for k in [k for k in x if k.endswith('color')]:
        x.pop(k)
    saa = x.get('saved_attribute_attributes', {})
    for k in [k for k in saa if k.endswith('color')]:
        saa.pop(k)


def p1_layout(P):
    """The face: Clock column | row labels | 8 stage strips with STEPS / NOTE / MOD
    pages | right panel that follows the page (Random / lane settings)."""
    R = boxes(P)
    DL = R['obj-296']['patcher']
    D = boxes(DL)
    H = R['obj-129']['patcher']
    HB = boxes(H)
    label_t = R['obj-126']
    numbox_t = R['obj-905']          # transparent number box (drawn by a jsui)
    tab_t = R['obj-73']
    button_t = copy.deepcopy(R['obj-980'])   # live.text button with Live's normal border (a copy: obj-980 itself
                                             # becomes a transparent click area under a drawn button)
    panel_t = R['obj-120']
    line_t = R['obj-918']
    UX = 1700.0                      # patching-view column for the new objects
    groups = {}                      # page / tab / rule group -> scripting names (sent to the script)
    place = {}                       # id -> presentation rect
    hidden_at_load = set()

    def grp(name, *gs):
        for g in gs:
            groups.setdefault(g, []).append(name)

    def put(bid, rect):
        place[bid] = [float(v) for v in rect]

    n = [2000]
    def nid():
        n[0] += 1
        return 'obj-%d' % n[0]

    def lab(text, rect, var=None, *gs, fs=8.0):
        bid = nid()
        b = clone(label_t, bid, [UX, 20.0 * (n[0] - 2000), rect[2], 16.0], None, text=text, ignoreclick=1,
                  fontsize=fs)
        b.pop('frgb', None)
        b.pop('textcolor', None)
        if var:
            b['varname'] = var
            grp(var, *gs)
        add_box(P, b)
        put(bid, rect)
        return bid

    def overlay(nbox_id, kind, lo, hi, var, refresh=True, vid=None, fs=None, plain=False):
        x = boxes(P)[nbox_id]
        vid = vid or nid()
        args = [kind, lo, hi] + ([fs or 9, 'plain'] if plain else ([fs] if fs else []))
        add_box(P, {'maxclass': DRAW_CLASS, 'id': vid, 'varname': var, 'filename': 'ml185box.js',
                    'jsarguments': args, 'numinlets': 1, 'numoutlets': 0, 'outlettype': [],
                    'parameter_enable': 0, 'border': 0, 'ignoreclick': 1,
                    'patching_rect': [UX + 300, 20.0 * (n[0] - 2000), 43.0, 13.0],
                    'presentation': 1, 'presentation_rect': [0, 0, 43, 13],
                    'annotation': x.get('annotation', '')})
        add_line(P, nbox_id, 0, vid, 0)
        if refresh:                                 # after load: re-send so the drawing is current
            add_line(P, 'obj-922', 0, nbox_id, 0)
        return vid

    def drawn_button(bid, vid, label, die, rect, var, *gs):
        """One push button drawn by ml185box.js (Live's button look, optional die before the label) over the
        live.text bid made fully transparent: the live.text takes the click and sends it on, the drawing
        flashes. Live draws nothing of ours over an opaque live.text."""
        drawn_over(bid, vid, ['button', 0, 1, 9, label] + (['die'] if die else []), rect, var, *gs)

    def drawn_over(bid, vid, args, rect, var, *gs):
        """A drawing by ml185box.js (args = its jsarguments) over the live.text bid made fully transparent."""
        b = boxes(P)[bid]
        b.update(text='', texton='')
        for attr in ('bgcolor', 'activebgcolor', 'bgoncolor', 'activebgoncolor', 'bordercolor', 'focusbordercolor',
                     'textcolor', 'activetextcolor', 'textoncolor', 'activetextoncolor', 'textoffcolor'):
            b[attr] = [0.0, 0.0, 0.0, 0.0]
            b.setdefault('saved_attribute_attributes', {})[attr] = {'expression': ''}
        add_box(P, {'maxclass': DRAW_CLASS, 'id': vid, 'varname': var, 'filename': 'ml185box.js',
                    'jsarguments': args,
                    'numinlets': 1, 'numoutlets': 0, 'outlettype': [], 'parameter_enable': 0, 'border': 0,
                    'ignoreclick': 1, 'patching_rect': [UX + 300, 900.0 + 20 * (int(vid[4:]) - 4480), 64.0, 13.0],
                    'presentation': 1, 'presentation_rect': list(rect), 'annotation': b.get('annotation', '')})
        add_line(P, bid, 0, vid, 0)
        put(vid, rect)
        grp(var, *gs)

    def to_numbox(x, keep_id):
        """A live.numbox drawn by ml185box.js, with x's parameter (same name, type, range)."""
        b = copy.deepcopy(numbox_t)
        b['id'] = keep_id
        b['varname'] = x['varname']
        b['annotation'] = x.get('annotation', '')
        b['patching_rect'] = x['patching_rect']
        b['saved_attribute_attributes']['valueof'] = copy.deepcopy(x['saved_attribute_attributes']['valueof'])
        b.pop('hidden', None)
        return b

    # -- 1. the 24 step controls leave the DialLayer bpatcher; its logic stays and is bridged with sends
    assert all(D[i]['maxclass'] == 'live.dial' for i in PITCH + VELO + AUX)
    moved = {}
    for did in AUX:                                 # Aux removed: the dials go, extra2 stays 0
        remove_box(DL, did)
    for k, did in enumerate(PITCH + VELO, 1):
        x = D[did]
        ins = [l['patchline'] for l in DL['lines'] if l['patchline']['destination'][0] == did]
        outs = [l['patchline'] for l in DL['lines'] if l['patchline']['source'][0] == did]
        assert all(l['source'][1] == 0 for l in outs)
        rid, sid = 'obj-%d' % (1200 + k), 'obj-%d' % (1300 + k)
        r = newobj(rid, 'r ---dlo%d' % k, 0, 1, [''], [700.0, 400.0 + 22 * k, 70.0, 20.0]); r['hidden'] = 1
        s = newobj(sid, 's ---dli%d' % k, 1, 0, [], [780.0, 400.0 + 22 * k, 70.0, 20.0]); s['hidden'] = 1
        add_box(DL, r)
        add_box(DL, s)
        for l in outs:
            add_line(DL, rid, 0, l['destination'][0], l['destination'][1])
        for l in ins:
            add_line(DL, l['source'][0], l['source'][1], sid, 0)
        remove_box(DL, did)
        cid = 'obj-%d' % (1100 + k)
        if did in PITCH:
            c = copy.deepcopy(x)
            c['id'] = cid
            for key in ('panelcolor',):
                c.pop(key, None)
            strip_colors(c)
            c['appearance'] = 0                     # knob with the value under it
            c['showname'] = 0
            c['shownumber'] = 1
            c['annotation'] = c.get('annotation', '')
        else:
            c = to_numbox(x, cid)
            if did in AUX:
                c['annotation'] = ('Aux value of this stage (0-127), sent with the stage\'s note as the MIDI CC '
                                   'or pitch bend chosen in the Setup tab.')
        c['patching_rect'] = [UX + 400, 20.0 * k, 44.0, 36.0 if did in PITCH else 13.0]
        add_box(P, c)
        so, ri = 'obj-%d' % (1400 + k), 'obj-%d' % (1500 + k)
        add_box(P, newobj(so, 's ---dlo%d' % k, 1, 0, [], [UX + 460, 20.0 * k, 70.0, 20.0]))
        add_box(P, newobj(ri, 'r ---dli%d' % k, 0, 1, [''], [UX + 540, 20.0 * k, 70.0, 20.0]))
        add_line(P, cid, 0, so, 0)
        add_line(P, ri, 0, cid, 0)
        moved[did] = cid
    R['obj-296']['presentation'] = 0                # the bpatcher is logic only now

    # -- 2. Setup controls leave the floating help window; both are saved with the set now
    assert HB['obj-267']['varname'] == 'Aux CC Number' and HB['obj-1']['varname'] == 'MIDI Thru'
    thru = clone(HB['obj-1'], 'obj-2102', [UX + 600, 30.0, 110.0, 15.0], None)
    strip_colors(thru)
    for bid in ('obj-267', 'obj-1', 'obj-3', 'obj-10', 'obj-11', 'obj-111', 'obj-2', 'obj-9'):
        if bid in HB:
            remove_box(H, bid)
    add_box(P, thru)
    add_box(P, newobj('obj-2105', 's ---midithru', 1, 0, [], [UX + 680, 90.0, 80.0, 20.0]))
    add_line(P, 'obj-2102', 0, 'obj-2105', 0)
    R = boxes(P)

    # -- 3. controls that change form, same parameters
    menu_t = R['obj-902']
    def to_menu(bid, enum=None, text=None):
        old = boxes(P)[bid]
        m = clone(menu_t, bid, old['patching_rect'], None, varname=old['varname'],
                  annotation=text or old.get('annotation', ''), fontsize=10.0)
        m['saved_attribute_attributes'] = copy.deepcopy(old['saved_attribute_attributes'])
        if enum:
            m['saved_attribute_attributes']['valueof']['parameter_enum'] = enum
        P['boxes'] = [b if b['box']['id'] != bid else {'box': m} for b in P['boxes']]
    to_menu('obj-267', R['obj-267']['saved_attribute_attributes']['valueof']['parameter_enum'] + ['1/4'],
            'Sequencer rate: one step is a 1/32, 1/16, 1/8 or 1/4 note. 1/4 runs straight (no swing).')
    for bid in ('obj-18', 'obj-72'):                # Coarse Tune, Sequence Length -> drawn value boxes
        nb = to_numbox(R[bid], bid)
        P['boxes'] = [b if b['box']['id'] != bid else {'box': nb} for b in P['boxes']]
    R = boxes(P)
    R['obj-18']['annotation'] = ('Transposes the sequence in semitones (formerly Coarse Tune). With a scale on, '
                                 'the transposed notes still snap to the scale. Key In adds on top.')
    for bid in ('obj-73', 'obj-115', 'obj-157', 'obj-106') + tuple(STAGE_MODE):
        strip_colors(R[bid])
    stage_div = ['obj-66', 'obj-78', 'obj-80', 'obj-95', 'obj-105', 'obj-107', 'obj-109', 'obj-110']
    stage_len = ['obj-35', 'obj-32', 'obj-11', 'obj-38', 'obj-46', 'obj-49', 'obj-52', 'obj-42']
    leds = ['obj-91', 'obj-92', 'obj-93', 'obj-94', 'obj-96', 'obj-97', 'obj-98', 'obj-99']
    assert [R[i]['varname'] for i in stage_div] == ['Stage%d Div' % k for k in range(1, 9)]
    assert [R[i]['varname'] for i in stage_len] == ['Stage%d Length' % k for k in range(1, 9)]
    assert [R[i]['varname'] for i in leds] == ['Stage%d LED' % k for k in range(1, 9)]
    for bid in stage_div + stage_len:
        strip_colors(R[bid])
    R['obj-157'].update(text='Stages', texton='Fixed')
    R['obj-106'].update(text='Info', texton='Info')
    R['obj-115']['annotation'] = ('Sets the repeat interval of every stage at once: every 1 (all), 2, 3 or 4 steps, '
                                  'for stages whose Mode repeats.')
    R['obj-106']['annotation'] = 'Opens the device info and the original author\'s notes.'

    # -- 4. new controls: page switch, panel tabs, row Random buttons
    sp = clone(tab_t, 'obj-2110', [UX + 760, 0.0, 120.0, 13.0], None, varname='Strip Page',
               annotation='Sequencer: pitch, velocity, Mode, Len, Probability and Slide of each stage, with Random '
                          'on the right. Mod: the knob rows of the three mod lanes, with each lane\'s Map, Min, Max '
                          'and Smooth on the right.')
    param(sp, parameter_enum=['Sequencer', 'Mod'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Strip Page', parameter_shortname='Page', parameter_mmin=0.0, parameter_mmax=1.0,
          parameter_invisible=1)
    strip_colors(sp)
    add_box(P, sp)
    add_box(P, newobj('obj-2112', 'prepend strippage', 1, 1, [''], [UX + 760, 60.0, 100.0, 20.0]))
    add_line(P, 'obj-2110', 0, 'obj-2112', 0)
    add_line(P, 'obj-2112', 0, 'obj-930', 0)

    row_rnd = (('obj-2120', 'Random Pitch Row', 'Randomizes the 8 pitch knobs (from Note Range; scale notes only '
                'when a scale is on).', 'obj-938'),
               ('obj-2121', 'Random Velocity Row', 'Randomizes the 8 velocity bars (from Velocity Range).',
                'obj-954'),
)
    for k, (bid, long, text, target) in enumerate(row_rnd):
        b = clone(button_t, bid, [UX + 900, 100.0 + 60 * k, 11.0, 11.0], None, varname=long, text='R',
                  annotation=text)
        param(b, parameter_enum=['val1', 'val2'], parameter_longname=long, parameter_shortname='Rnd',
              parameter_invisible=2)
        b.update(text='', texton='')
        for attr in ('bgcolor', 'activebgcolor', 'bordercolor', 'focusbordercolor', 'textcolor',
                     'activetextcolor', 'activetextoncolor', 'textoffcolor'):
            b[attr] = [0.0, 0.0, 0.0, 0.0]
            b['saved_attribute_attributes'][attr] = {'expression': ''}
        add_box(P, b)
        rb, s1 = nid(), nid()
        add_box(P, newobj(rb, 'route bang', 2, 2, ['', ''], [UX + 920, 100.0 + 60 * k, 60.0, 20.0]))
        add_box(P, newobj(s1, 'sel 1', 2, 2, ['bang', ''], [UX + 990, 120.0 + 60 * k, 40.0, 20.0]))
        if target is None:                          # Aux: the DialLayer's existing 8x random 127 chain
            sa = nid()
            add_box(P, newobj(sa, 's ---rndaux', 1, 0, [], [UX + 920, 140.0 + 60 * k, 70.0, 20.0]))
            target = sa
            assert D['obj-82']['text'] == 'uzi 8'
            ra = 'obj-1399'
            r = newobj(ra, 'r ---rndaux', 0, 1, [''], [700.0, 380.0, 70.0, 20.0]); r['hidden'] = 1
            add_box(DL, r)
            add_line(DL, ra, 0, 'obj-82', 0)
        add_line(P, bid, 0, rb, 0)
        add_line(P, rb, 0, target, 0)
        add_line(P, rb, 1, s1, 0)
        add_line(P, s1, 0, target, 0)
    R = boxes(P)

    # -- 5. the layout table, 770 x 169:
    #   left column  the settings in one line: Length, Rate, Direction, Swing, Transpose, Restart,
    #                Source, Scale, Key In (name left, control right)
    #   sequencer    Sequencer / Mod tabs on top; Sequencer = pitch, velocity, Mode, Len, Prob, Gate,
    #                Slide per stage; Mod = four knob rows (lanes A-D) with their Map buttons
    #   right panel  follows the tabs: Random (Sequencer), lane Map / Min / Max / Smooth (Mod)
    for b in P['boxes']:                            # start from an empty face
        if b['box'].get('presentation'):
            b['box']['presentation'] = 0
    L = 13
    # Left column (calmer look): names 6..50, controls 52..172, 9 pt; timing rows, a gap, then pitch
    LX, NW, CX, CW = 6, 52, 58, 114     # names 6..58 (room for wider text on Windows), controls 58..172
    # one grid with the sequencer: top on the tab row (2), Scale on the Prob row (121),
    # Bend on the Slide row (137), Key In on the Lock row (153)
    YL = {'Length': 2, 'Rate': 19, 'Direction': 36, 'Swing': 53, 'Transpose': 70, 'Restart': 87, 'Gate': 104,
          'Scale': 121, 'Bend': 137, 'Key In': 153}
    def name(text, var=None, *gs):
        # Key In sits 1 px up: the switch beside it draws its text higher than the boxes of the other rows
        lab(text, [LX, YL[text] - (1 if text == 'Key In' else 0), NW, 13], var, *gs, fs=9.0)
    def lc(bid, key, x=CX, w=CW):
        put(bid, [x, YL[key], w, 13])
        if R[bid]['maxclass'] in ('live.tab', 'live.menu', 'live.text'):
            R[bid]['fontsize'] = 9.0

    name('Length'); lc('obj-72', 'Length')
    v72 = overlay('obj-72', 'int', 1, 8, 'Sequence Length Display', plain=True); put(v72, [CX, YL['Length'], CW, 13])
    R['obj-72']['annotation'] = 'How many stages the sequence plays, from stage 1 (1-8).'
    boxes(P)[v72]['annotation'] = R['obj-72']['annotation']
    R = boxes(P)
    name('Rate'); lc('obj-267', 'Rate')
    name('Direction'); lc('obj-73', 'Direction')
    name('Swing'); lc('obj-291', 'Swing'); put('obj-970', [CX, YL['Swing'], CW, 13])
    R['obj-970']['jsarguments'] = R['obj-970']['jsarguments'][:3] + [9, 'plain']
    name('Transpose'); lc('obj-18', 'Transpose')
    v18 = overlay('obj-18', 'st', -24, 24, 'Coarse Tune Display', plain=True); put(v18, [CX, YL['Transpose'], CW, 13])
    p3_controls(P, tab_t, R['obj-157'])
    to_menu('obj-3301', ['Off', '1 bar', '2 bars', '4 bars', '8 bars'])
    to_menu('obj-901', ['Off', 'Live', 'Own', 'Slices'])
    boxes(P)['obj-901']['saved_attribute_attributes']['valueof']['parameter_mmax'] = 3.0
    R = boxes(P)
    name('Restart'); lc('obj-3301', 'Restart')
    # Gate: the length of every note that does not slide, in the free row under Restart, drawn
    # like Swing and Transpose. ml185stage.js scales its note length table; nothing in the timing path
    # changes. Fixed ids (obj-4560 box, 4561 drawing, 4562 label), so the generated ids stay as they were.
    gb = copy.deepcopy(numbox_t)
    gb.update(id='obj-4560', varname='Gate', annotation='', patching_rect=[UX + 900, 1100.0, CW, 13.0])
    gb.pop('hidden', None)
    param(gb, parameter_initial=[100], parameter_initial_enable=1, parameter_type=1, parameter_unitstyle=5,
          parameter_mmin=1.0, parameter_mmax=100.0, parameter_steps=0, parameter_longname='Gate',
          parameter_shortname='Gate', parameter_invisible=0)
    add_box(P, gb)
    gl = clone(label_t, 'obj-4562', [UX, 1100.0, NW, 16.0], None, text='Gate', ignoreclick=1, fontsize=9.0,
               varname='lbl Gate')
    gl.pop('frgb', None)
    gl.pop('textcolor', None)
    add_box(P, gl)
    put('obj-4562', [LX, YL['Gate'], NW, 13])
    R = boxes(P)
    lc('obj-4560', 'Gate')
    vg = overlay('obj-4560', 'ipct', 1, 100, 'Gate Display', vid='obj-4561', plain=True); put(vg, [CX, YL['Gate'], CW, 13])
    R = boxes(P)
    # Scale row: Source, Root, Scale (the LIVE display pair takes the Root and Scale cells)
    name('Scale')
    # Root, then one menu Off / Live / own scales (Source merged in, so the scale
    # names get the room); Scale Source and Scale stay the stored parameters, off the face
    lc('obj-902', 'Scale', CX, 26); grp('Scale Root', 'own')
    lc('obj-909', 'Scale', CX, 26); grp('Live Root Display', 'live')
    ss = scale_select(P, R['obj-902'])
    R = boxes(P)
    lc(ss, 'Scale', CX + 28, CW - 28)
    # Slices: the root cell shows how many slices the Simpler after the device has (0: none slicing)
    add_box(P, {'maxclass': DRAW_CLASS, 'id': 'obj-4540', 'varname': 'Slice Count Display', 'filename': 'ml185box.js',
                'jsarguments': ['int', 0, 127, 9, 'plain'], 'numinlets': 1, 'numoutlets': 0, 'outlettype': [],
                'parameter_enable': 0, 'border': 0, 'ignoreclick': 1, 'patching_rect': [3300.0, 1060.0, 26.0, 13.0],
                'presentation': 1, 'presentation_rect': [0, 0, 26, 13], 'annotation': ''})
    put('obj-4540', [CX, YL['Scale'], 26, 13]); grp('Slice Count Display', 'slices')
    # Bend: only while a microtonal (~) scale is on
    put('obj-914', [LX, YL['Bend'], NW, 13]); grp('Bend Range Label', 'micro')
    R['obj-914'].update(fontsize=9.0)
    put('obj-904', [CX, YL['Bend'], CW, 13]); grp('Bend Range', 'micro')
    put('obj-971', [CX, YL['Bend'], CW, 13]); grp('Bend Range Display', 'micro')
    R['obj-971']['jsarguments'] = R['obj-971']['jsarguments'][:3] + [9, 'plain']
    # Key In: one switch Off / Latch / Hold (writes the Key In and Key Mode parameters)
    name('Key In')
    ks = key_in_switch(P, tab_t)
    R = boxes(P)
    lc(ks, 'Key In', CX, 85)                         # "Latch" needs 28 px per segment
    # MIDI Thru sits beside Key In (the Setup tab is gone)
    put('obj-2102', [CX + 87, YL['Key In'], CW - 87, 13])
    R['obj-2102'].update(text='Thru', texton='Thru', fontsize=9.0,
                         annotation='MIDI Thru: passes the MIDI coming into this track (CCs, pitch bend, and the '
                                    'notes while Key In is off) on to the instrument along with the sequence.')
    sep1 = clone(line_t, nid(), [UX, 400.0, 5.0, 155.0], None); add_box(P, sep1); put(sep1['id'], [173, 4, 5, 161])

    # Sequencer: tabs on top, row names and row dice (x 164-206), stage strips from x 208
    GX, SX = 180, 224
    put('obj-2110', [SX, 2, 120, 13]); R['obj-2110']['fontsize'] = 10.0   # starts on stage 1's column
    # fold button: the settings column folds away, the device narrows (ml185scale.js fold);
    # lit only while folded, a reminder that something is hidden
    fb = clone(R['obj-157'], 'obj-4410', [UX + 900, 500.0, 13.0, 13.0], None, varname='Settings Fold',
               text='\u2039', texton='\u203a', fontsize=9.0,
               annotation='Folds the settings column (Length to Key In) away so the device takes less room; '
                          'click again to open it. Saved with the set; the settings keep working while folded.')
    param(fb, parameter_enum=['Open', 'Folded'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Settings Fold', parameter_shortname='Settings', parameter_mmin=0.0,
          parameter_mmax=1.0, parameter_invisible=1)
    strip_colors(fb)
    add_box(P, fb)
    add_box(P, newobj('obj-4411', 'prepend fold', 1, 1, [''], [UX + 900, 530.0, 80.0, 20.0]))
    add_line(P, 'obj-4410', 0, 'obj-4411', 0)
    add_line(P, 'obj-4411', 0, 'obj-930', 0)
    put('obj-4410', [GX, 2, SX - 4 - GX, 13])
    # drawn: a gear and an arrow, lit while folded (bigger, with a settings sign)
    drawn_over('obj-4410', 'obj-4485', ['fold', 0, 1], [GX, 2, SX - 4 - GX, 13], 'Settings Fold Button')
    def dice(bid, rect, var, *gs):
        put(bid, rect)
        d = overlay(bid, 'dice', 0, 1, var, refresh=False)
        put(d, rect)
        if gs:
            grp(var, *gs)
    # Pitch and Vel sit 2 px above their dice's boxes, like the lane letters: Live draws label text low
    lab('Pitch', [GX, 44, 28, 12], 'lbl Pitch', 'notmod', fs=9.0); grp('Random Pitch Row', 'notmod')
    dice('obj-2120', [208, 46, 13, 13], 'Random Pitch Row Dice', 'notmod')
    lab('Vel', [GX, 72, 27, 12], 'lbl Vel', 'notmod', fs=9.0); grp('Random Velocity Row', 'notmod')
    dice('obj-2121', [208, 74, 13, 13], 'Random Velocity Row Dice', 'notmod')
    # rows (the Lock row): Vel 74, Mode 90, Len 106, Prob 121, Slide 137, Lock 153
    for text, y, g in (('Mode', 90, 'steps'), ('Len', 105, 'steps'), ('Prob', 121, 'note'), ('Slide', 137, 'note'),
                       ('Lock', 153, 'note')):
        lab(text, [GX, y, 40, 12], 'lbl ' + text, g, fs=9.0)
    lane_ids = p5_controls(P, numbox_t, button_t, R['obj-157'], boxes(P)[moved[PITCH[0]]])
    for l, letter in enumerate(LANES):             # Mod rows: knobs y 31 + 45 * l, 40 high; letter and die
        y = 31 + 45 * l
        # the letter's box sits 2 px above the die's: Live draws the text low in a label, and the letter
        # then stands on the die's centre line
        lab(letter, [GX, y + 13, 20, 12], 'lbl Lane ' + letter, 'mod', fs=9.0)
        long = 'Random Lane %s' % letter
        b = clone(button_t, 'obj-%d' % (4390 + l), [UX + 900, 300.0 + 60 * l, 11.0, 11.0], None, varname=long,
                  annotation='Randomizes the 8 knobs of lane %s (0-100 %%).' % letter)
        param(b, parameter_enum=['val1', 'val2'], parameter_longname=long, parameter_shortname='Rnd %s' % letter,
              parameter_invisible=2)
        b.update(text='', texton='')
        for attr in ('bgcolor', 'activebgcolor', 'bordercolor', 'focusbordercolor', 'textcolor',
                     'activetextcolor', 'activetextoncolor', 'textoffcolor'):
            b[attr] = [0.0, 0.0, 0.0, 0.0]
            b['saved_attribute_attributes'][attr] = {'expression': ''}
        add_box(P, b)
        rb, s1, rl = ('obj-%d' % (4393 + 3 * l + k) for k in range(3))
        add_box(P, newobj(rb, 'route bang', 2, 2, ['', ''], [UX + 920, 300.0 + 60 * l, 60.0, 20.0]))
        add_box(P, newobj(s1, 'sel 1', 2, 2, ['bang', ''], [UX + 990, 320.0 + 60 * l, 40.0, 20.0]))
        add_box(P, message(rl, 'randomlane %d' % l, [UX + 1040, 320.0 + 60 * l, 80.0, 20.0]))
        for a_, ao, b_, bi in ((b['id'], 0, rb, 0), (rb, 0, rl, 0), (rb, 1, s1, 0), (s1, 0, rl, 0),
                               (rl, 0, 'obj-930', 0)):
            add_line(P, a_, ao, b_, bi)
        grp(long, 'mod')
        dice(b['id'], [208, y + 15, 13, 13], long + ' Dice', 'mod')

    # Shift: drawn arrow buttons that take their own click (no parameter, no live.text under
    # them); the bang goes to a message for ml185scale.js. Sequencer page: < and > on the tab line over stages
    # 7 and 8, rotating every stage row. Mod page: < and > under each lane's letter and die, rotating that
    # lane's knobs. Ids obj-4800..4807 (drawings), obj-4810..4817 (messages).
    def arrow(k, msg, icon, rect, var, *gs):
        vid, mid = 'obj-%d' % (4800 + k), 'obj-%d' % (4810 + k)
        add_box(P, {'maxclass': DRAW_CLASS, 'id': vid, 'varname': var, 'filename': 'ml185box.js',
                    'jsarguments': ['button', 0, 1, 9, '', icon], 'numinlets': 1, 'numoutlets': 1, 'outlettype': [''],
                    'parameter_enable': 0, 'border': 0, 'ignoreclick': 0,
                    'patching_rect': [UX + 1200, 1300.0 + 30 * k, rect[2], rect[3]],
                    'presentation': 1, 'presentation_rect': list(rect), 'annotation': ''})
        add_box(P, message(mid, msg, [UX + 1260, 1300.0 + 30 * k, 90.0, 20.0]))
        add_line(P, vid, 0, mid, 0)
        add_line(P, mid, 0, 'obj-930', 0)
        put(vid, rect)
        grp(var, *gs)
    arrow(0, 'shift -1', 'left', [SX + 52 * 6 + 3, 2, 43, 13], 'Shift Left Button', 'steps')
    arrow(1, 'shift 1', 'right', [SX + 52 * 7 + 3, 2, 43, 13], 'Shift Right Button', 'steps')
    for l, letter in enumerate(LANES):
        y = 31 + 45 * l
        arrow(2 + 2 * l, 'laneshift %d -1' % l, 'left', [GX, y + 30, 20, 13], 'Lane %s Shift Left Button' % letter, 'mod')
        arrow(3 + 2 * l, 'laneshift %d 1' % l, 'right', [GX + 22, y + 30, 20, 13], 'Lane %s Shift Right Button' % letter, 'mod')

    note_ids = p2_controls(P, numbox_t, R['obj-157'])
    mode_sel, len_view = p6_controls(P, R['obj-902'], stage_len)
    vel_ids = [moved[d] for d in VELO]
    for i in range(8):
        x0 = SX + 52 * i
        # the column's panel is named so ml185stage.js can hide it for a stage that does not play
        pnl = clone(panel_t, nid(), [UX, 500.0 + 20 * i, 49.0, 151.0], None, background=1, ignoreclick=1,
                    varname='Stage%d Panel' % (i + 1))
        add_box(P, pnl); put(pnl['id'], [x0, 16, 49, 151])
        led = R[leds[i]]
        strip_colors(led)
        led.update(text=str(i + 1), texton=str(i + 1), ignoreclick=1, fontsize=9.0)
        put(leds[i], [x0 + 3, 18, 43, 11])
        # On: a stored, automatable toggle over the stage number, fully transparent so the number
        # shows through; ml185stage.js draws the number inactive while the stage is off. Both pages, so
        # no page group. Ids obj-4541..4548.
        sw = clone(R['obj-157'], 'obj-%d' % (4541 + i), [3400.0, 1100.0 + 20 * i, 43.0, 11.0], None,
                   varname='Stage%d On' % (i + 1), annotation='', text='', texton='')
        param(sw, parameter_enum=['off', 'on'], parameter_initial=[1], parameter_initial_enable=1,
              parameter_longname='Stage%d On' % (i + 1), parameter_shortname='St%d On' % (i + 1),
              parameter_mmin=0.0, parameter_mmax=1.0, parameter_invisible=0)
        for attr in ('bgcolor', 'activebgcolor', 'bgoncolor', 'activebgoncolor', 'bordercolor', 'focusbordercolor',
                     'textcolor', 'activetextcolor', 'textoncolor', 'activetextoncolor', 'textoffcolor'):
            sw[attr] = [0.0, 0.0, 0.0, 0.0]
            sw.setdefault('saved_attribute_attributes', {})[attr] = {'expression': ''}
        add_box(P, sw)
        put(sw['id'], [x0 + 3, 18, 43, 11])
        put(moved[PITCH[i]], [x0, 31, 49, 40]); grp('PitchDial%d' % (i + 1), 'notmod')
        put(vel_ids[i], [x0 + 3, 74, 43, 13]); grp('VelocityDial%d' % (i + 1), 'notmod')
        vv = overlay(vel_ids[i], 'int', 0, 127, 'VelocityDial%d Display' % (i + 1), refresh=False)
        put(vv, [x0 + 3, 74, 43, 13]); grp('VelocityDial%d Display' % (i + 1), 'notmod')
        # Mode dropdown and Len cells; the Mode / Div / Length tabs stay hidden as the parameters
        put(mode_sel[i], [x0 + 3, 90, 43, 13]); grp('Stage%d Mode Select' % (i + 1), 'steps')
        put(len_view[i], [x0 + 1, 106, 47, 12]); grp('Stage%d Len View' % (i + 1), 'steps')
        for kind, y in (('Probability', 121),):
            bid = note_ids[kind][i]
            put(bid, [x0 + 3, y, 43, 13]); grp('Stage%d %s' % (i + 1, kind), 'note')
            vo = overlay(bid, 'ipct', 0, 100, 'Stage%d %s Display' % (i + 1, kind))
            put(vo, [x0 + 3, y, 43, 13]); grp('Stage%d %s Display' % (i + 1, kind), 'note')
        put(note_ids['Slide'][i], [x0 + 3, 137, 43, 13]); grp('Stage%d Slide' % (i + 1), 'note')
        boxes(P)[note_ids['Slide'][i]]['fontsize'] = 9.0
        # Lock: the stage keeps everything when a random tool runs; a stored toggle,
        # drawn as a padlock in the Slide row's pill
        lk = clone(R['obj-157'], 'obj-%d' % (4510 + i), [3400.0, 900.0 + 20 * i, 43.0, 13.0], None,
                   varname='Stage%d Lock' % (i + 1), annotation='')
        param(lk, parameter_enum=['off', 'on'], parameter_initial=[0], parameter_initial_enable=1,
              parameter_longname='Stage%d Lock' % (i + 1), parameter_shortname='St%d Lock' % (i + 1),
              parameter_mmin=0.0, parameter_mmax=1.0, parameter_invisible=1)
        add_box(P, lk)
        put(lk['id'], [x0 + 3, 153, 43, 13]); grp('Stage%d Lock' % (i + 1), 'note')
        drawn_over(lk['id'], 'obj-%d' % (4520 + i), ['lock', 0, 1], [x0 + 3, 153, 43, 13],
                   'Stage%d Lock Icon' % (i + 1), 'note')
        for l, letter in enumerate(LANES):                       # Mod: lane knobs
            put(lane_ids['stage'][l][i], [x0, 31 + 45 * l, 49, 40]); grp('Mod %s Stage%d' % (letter, i + 1), 'mod')
    sep2 = clone(line_t, nid(), [UX, 420.0, 5.0, 155.0], None); add_box(P, sep2); put(sep2['id'], [639, 4, 5, 161])

    # Right panel (x 646, w 134), switched by the Sequencer / Mod tabs: Random beside the stages;
    # beside each lane's knob row its Map (X unmaps), then Min, Max and Smooth
    PX, PW = 646, 134
    # rows on the sequencer's lines: Global / Reset on the tab row, ticks in the pitch row, the range
    # boxes on the Mode and Prob rows, Mutate | Auto | 4 8 16 on the bottom (Lock) row; columns 646..710 and
    # 712..780
    C2 = PX + 66
    # Global (was RANDOM ALL; die + "Global" as ONE button) and Reset in one row,
    # both drawn (Live draws no die over its own opaque button)
    put('obj-980', [PX, 2, C2 - 2 - PX, 13]); grp('Random All', 'tab:random')
    R['obj-980'].update(fontsize=9.0,
                        annotation='Global: randomizes the parts ticked below for all 8 stages (pitch from Note '
                                   'range and the scale, velocity from Velocity range). Reset puts every '
                                   'stage row back to its defaults.')
    rs = clone(R['obj-980'], 'obj-4430', [UX + 900, 600.0, 53.0, 16.0], None, varname='Reset Ticked',
               text='Reset', texton='Reset', fontsize=9.0,
               annotation='Reset: every unlocked stage goes back to its defaults (pitch 0 st, velocity 120, '
                          'Mode once, Len 1, Prob 100 %, Slide off, stage on), whatever is ticked.')
    param(rs, parameter_longname='Reset Ticked', parameter_shortname='Reset')
    add_box(P, rs)
    add_box(P, newobj('obj-4431', 'route bang', 2, 2, ['', ''], [UX + 960, 600.0, 60.0, 20.0]))
    add_box(P, newobj('obj-4432', 'sel 1', 2, 2, ['bang', ''], [UX + 960, 630.0, 40.0, 20.0]))
    add_box(P, message('obj-4433', 'resetall', [UX + 1030, 630.0, 55.0, 20.0]))
    for a_, ao, b_, bi in (('obj-4430', 0, 'obj-4431', 0), ('obj-4431', 0, 'obj-4433', 0), ('obj-4431', 1, 'obj-4432', 0),
                           ('obj-4432', 0, 'obj-4433', 0), ('obj-4433', 0, 'obj-930', 0)):
        add_line(P, a_, ao, b_, bi)
    put('obj-4430', [C2 + 34, 2, PX + PW - C2 - 34, 13]); grp('Reset Ticked', 'tab:random')
    drawn_button('obj-980', 'obj-4480', 'Global', True, [PX, 2, C2 - 2 - PX, 13], 'Global Button', 'tab:random')
    drawn_button('obj-4430', 'obj-4481', 'Reset', False, [C2 + 34, 2, PX + PW - C2 - 34, 13], 'Reset Button', 'tab:random')
    # Undo: an arrow; each click restores what one random tool changed, going back
    # up to 32 tools (ml185scale.js undo); both pages
    bk = clone(button_t, 'obj-4530', [UX + 900, 900.0, 32.0, 13.0], None, varname='Undo', annotation='')
    param(bk, parameter_enum=['val1', 'val2'], parameter_longname='Undo', parameter_shortname='Undo',
          parameter_invisible=2, parameter_initial_enable=0)
    add_box(P, bk)
    add_box(P, newobj('obj-4531', 'route bang', 2, 2, ['', ''], [UX + 960, 900.0, 60.0, 20.0]))
    add_box(P, newobj('obj-4532', 'sel 1', 2, 2, ['bang', ''], [UX + 960, 930.0, 40.0, 20.0]))
    add_box(P, message('obj-4533', 'undo', [UX + 1030, 930.0, 40.0, 20.0]))
    for a_, ao, b_, bi in (('obj-4530', 0, 'obj-4531', 0), ('obj-4531', 0, 'obj-4533', 0), ('obj-4531', 1, 'obj-4532', 0),
                           ('obj-4532', 0, 'obj-4533', 0), ('obj-4533', 0, 'obj-930', 0)):
        add_line(P, a_, ao, b_, bi)
    put('obj-4530', [C2, 2, 32, 13])
    drawn_over('obj-4530', 'obj-4534', ['button', 0, 1, 9, '', 'undo'], [C2, 2, 32, 13], 'Undo Button')
    # 2 x 4 ticks from the stage-number row: what each note is on the left, timing on the right
    for col, ids in enumerate((('obj-981', 'obj-982', 'obj-4490', 'obj-4491'),      # Pitch Velocity Prob Slide
                               ('obj-983', 'obj-984', 'obj-985', 'obj-986'))):      # Mode Div Len Seq Length
        for k, bid in enumerate(ids):
            put(bid, [PX if col == 0 else C2, 18 + 13 * k, 64 if col == 0 else PX + PW - C2, 12])
            grp(R[bid]['varname'], 'tab:random')
    put('obj-915', [PX, 77, 80, 12]); grp('lbl Note Range', 'tab:random')
    put('obj-905', [PX, 90, 64, 13]); put('obj-972', [PX, 90, 64, 13])
    put('obj-906', [C2, 90, PX + PW - C2, 13]); put('obj-973', [C2, 90, PX + PW - C2, 13])
    put('obj-926', [PX, 108, 90, 12]); grp('lbl Velocity Range', 'tab:random')
    put('obj-907', [PX, 121, 64, 13]); put('obj-974', [PX, 121, 64, 13])
    put('obj-908', [C2, 121, PX + PW - C2, 13]); put('obj-975', [C2, 121, PX + PW - C2, 13])
    for bid in ('obj-905', 'obj-906', 'obj-907', 'obj-908', 'obj-972', 'obj-973', 'obj-974', 'obj-975'):
        grp(R[bid]['varname'], 'tab:random')
    for bid in ('obj-972', 'obj-973', 'obj-974', 'obj-975'):     # plain numbers, like the left column
        R[bid]['jsarguments'] = R[bid]['jsarguments'][:3] + [9, 'plain']
    # bottom row, the same on both pages: Mutate | Auto | every 4 / 8 / 16 bars
    p4_controls(P, button_t, tab_t, R['obj-157'])
    R = boxes(P)
    for ids, g in ((('obj-3401', 'obj-3402', 'obj-3407'), 'tab:random'), (('obj-4700', 'obj-4704', 'obj-4706'), 'tab:mod')):
        for bid, x, w in zip(ids, (PX, PX + 48, PX + 80), (46, 30, PW - 80)):
            put(bid, [x, 153, w, 13]); grp(R[bid]['varname'], g)
    # Mutate is drawn like Global: since it is mappable it is a toggle underneath (mappable_buttons),
    # and Live's own toggle would stay lit after every second press. Ids obj-4488, obj-4489.
    drawn_button('obj-3401', 'obj-4488', 'Mutate', False, [PX, 153, 46, 13], 'Mutate Button', 'tab:random')
    drawn_button('obj-4700', 'obj-4489', 'Mutate', False, [PX, 153, 46, 13], 'Lane Mutate Button', 'tab:mod')
    # "Bars" over 4 / 8 / 16, like the range labels 13 px above their boxes; the Mod page
    # has lane C's boxes on that line, so only the Sequencer page has it
    hb = lab('Bars', [PX + 80, 140, PW - 80, 12], 'lbl Mutate Bars', 'tab:random', fs=9.0)
    boxes(P)[hb]['textjustification'] = 1
    for bid in ('obj-980', 'obj-981', 'obj-982', 'obj-983', 'obj-984', 'obj-985', 'obj-986', 'obj-4490', 'obj-4491', 'obj-915', 'obj-926'):
        R[bid]['fontsize'] = 9.0
    # top row, as Global / Reset on the Sequencer page: a die and Random (all three lanes' knobs to random
    # values) and Reset (all to 0 %); Min / Max / Smooth and the mapping stay
    add_box(P, message('obj-4469', 'randomlanes', [UX + 1040, 760.0, 75.0, 20.0]))
    add_box(P, message('obj-4470', 'resetlanes', [UX + 1040, 790.0, 70.0, 20.0]))
    add_line(P, 'obj-4469', 0, 'obj-930', 0)
    add_line(P, 'obj-4470', 0, 'obj-930', 0)
    for k, (bid, long, short, text, target, rect, info) in enumerate((
            ('obj-4463', 'Random Lanes', 'Rnd Lanes', 'Random', 'obj-4469', [PX, 2, C2 - 2 - PX, 13],
             'Random: sets the knobs of all three lanes to random values. Min, Max, Smooth and the mappings stay.'),
            ('obj-4466', 'Reset Lanes', 'Rst Lanes', 'Reset', 'obj-4470', [C2 + 34, 2, PX + PW - C2 - 34, 13],
             'Reset: the Mod page back to its defaults (knobs 0 %, Min 0 %, Max 100 %, Smooth 0 ms). The '
             'mappings stay.'))):
        b = clone(R['obj-980'] if text else button_t, bid, [UX + 900, 760.0 + 30 * k, 13.0, 13.0], None,
                  varname=long, text=text, texton=text, fontsize=9.0, annotation=info)
        param(b, parameter_enum=['val1', 'val2'], parameter_longname=long, parameter_shortname=short,
              parameter_invisible=2, parameter_initial_enable=0)
        if not text:                              # the die: a transparent button under the drawn die
            for attr in ('bgcolor', 'activebgcolor', 'bordercolor', 'focusbordercolor', 'textcolor',
                         'activetextcolor', 'activetextoncolor', 'textoffcolor'):
                b[attr] = [0.0, 0.0, 0.0, 0.0]
                b['saved_attribute_attributes'][attr] = {'expression': ''}
        add_box(P, b)
        rb, s1 = 'obj-%d' % (int(bid[4:]) + 1), 'obj-%d' % (int(bid[4:]) + 2)
        add_box(P, newobj(rb, 'route bang', 2, 2, ['', ''], [UX + 960, 760.0 + 30 * k, 60.0, 20.0]))
        add_box(P, newobj(s1, 'sel 1', 2, 2, ['bang', ''], [UX + 1000, 760.0 + 30 * k, 40.0, 20.0]))
        for a_, ao, b_, bi in ((bid, 0, rb, 0), (rb, 0, target, 0), (rb, 1, s1, 0), (s1, 0, target, 0)):
            add_line(P, a_, ao, b_, bi)
        put(bid, rect)
        grp(long, 'tab:mod')
        drawn_button(bid, 'obj-%d' % (4482 + k), text, text == 'Random', rect, long + ' Button', 'tab:mod')
    for text, x, w in (('Min', PX, 40), ('Max', PX + 42, 40), ('Smooth', PX + 84, 50)):   # centred over the boxes
        hb = lab(text, [x, 18, w, 12], 'lbl Mod ' + text, 'tab:mod', fs=9.0)
        boxes(P)[hb]['textjustification'] = 1
    for l, letter in enumerate(LANES):
        y = 31 + 45 * l                         # Map 13 + 2 + boxes 13 from the top of the knob row, so the
        # Map over the Min and Max columns, its x at the left edge of the Smooth column: one grid with the
        # boxes below
        put(lane_ids['map'][l], [PX, y, 82, 13]); grp('Mod %s Map' % letter, 'tab:mod')   # Mutate row fits
        put(lane_ids['unmap'][l], [PX + 84, y, 13, 13]); grp('Mod %s Unmap' % letter, 'tab:mod')
        for k, (part, x, w, kind, hi) in enumerate((('Min', PX, 40, 'ipct', 100), ('Max', PX + 42, 40, 'ipct', 100),
                                                     ('Smooth', PX + 84, 50, 'ms', 2000))):
            bid = lane_ids[part.lower()][l]
            var = 'Mod %s %s' % (letter, part)
            put(bid, [x, y + 15, w, 13]); grp(var, 'tab:mod')
            vo = overlay(bid, kind, 0, hi, var + ' Display', vid='obj-%d' % (3741 + 3 * l + k), plain=True)
            put(vo, [x, y + 15, w, 13]); grp(var + ' Display', 'tab:mod')

    # Reused labels get scripting names for their tab
    R = boxes(P)
    for bid, var in (('obj-915', 'lbl Note Range'), ('obj-926', 'lbl Velocity Range')):
        R[bid]['varname'] = var
        R[bid]['text'] = 'Note range' if bid == 'obj-915' else 'Velocity range'
    R['obj-914']['text'] = 'Bend'
    for bid in ('obj-914', 'obj-915', 'obj-926'):   # the 2009 template's fixed near-black text -> theme colour
        for attr in ('textcolor', 'frgb'):
            R[bid].pop(attr, None)
            R[bid].get('saved_attribute_attributes', {}).pop(attr, None)

    for bid, rect in place.items():
        R[bid]['presentation'] = 1
        R[bid]['presentation_rect'] = rect

    # the settings column (x < 178) hides while folded; give every piece a scripting name for that
    Rn = boxes(P)
    k_left = 0
    for bid, rect in sorted(place.items()):
        if rect[0] < 178:
            if not Rn[bid].get('varname'):
                k_left += 1
                Rn[bid]['varname'] = 'Settings Item %d' % k_left
            grp(Rn[bid]['varname'], 'left')
    # what is hidden before the script runs: everything off the default page (Sequencer) and tab (Rand)
    shown = {'steps', 'note', 'notmod', 'tab:random', 'own', 'left'}
    for name, gs in names_by_group(groups).items():
        hide = not all(g in shown for g in gs)
        for b in P['boxes']:
            if b['box'].get('varname') == name:
                b['box']['hidden'] = 1 if hide else 0

    # the script learns the groups at load, then shows / hides on page, tab, source and scale changes
    lb, tr = 'obj-2190', 'obj-2191'
    msgs = []
    for k, (g, names) in enumerate(sorted(groups.items())):
        mid = 'obj-%d' % (2192 + k)
        add_box(P, message(mid, 'uigroup %s %s' % (g, ' '.join('"%s"' % s for s in names)),
                           [UX + 1100, 200.0 + 30 * k, 300.0, 20.0]))
        add_line(P, mid, 0, 'obj-930', 0)
        msgs.append(mid)
    add_box(P, newobj(lb, 'loadbang', 1, 1, ['bang'], [UX + 1100, 140.0, 60.0, 20.0]))
    add_box(P, newobj(tr, 't ' + ' '.join(['b'] * len(msgs)), 1, len(msgs), ['bang'] * len(msgs),
                      [UX + 1100, 170.0, 200.0, 20.0]))
    add_line(P, lb, 0, tr, 0)
    for k, mid in enumerate(msgs):
        add_line(P, tr, k, mid, 0)
    for l in DL['lines']:                           # the bpatcher shows its editing view if ever opened
        l['patchline']['hidden'] = 1
    assert n[0] < 2100, 'generated ids ran into the fixed range'
    return groups


NOTE_TEXT = {
    'Probability': ('Chance that this stage plays its note (0-100 %). Rolled for every note the stage plays: '
                    'once for once and hold, on every repeat for 1/4, 1/3, 1/2 and all. When the roll fails the '
                    'stage stays silent, but its light still moves.'),
    'Slide': ('Holds this stage\'s note until the next note has started, so a mono synth with legato or '
              'glide slides into it, 303 style. If the next note has the same pitch, the two are tied (no new '
              'attack). If no note follows (a silent stage or a failed Probability roll), the note is held 3/4 '
              'of a step past where that next note would have started.'),
}


def p2_controls(P, numbox_t, toggle_t):
    """Stage1-8 Probability (drawn number boxes) and Slide (toggle). Ids obj-3001..3024, three per stage:
    Probability, a free slot (once Gate, removed), Slide. Returns
    {kind: [8 ids]}; placed by p1_layout."""
    ids = {'Probability': [], 'Slide': []}
    k = 3000
    for i in range(1, 9):
        for kind, lo, init in (('Probability', 0, 100),):
            k += 1
            b = copy.deepcopy(numbox_t)
            b['id'] = 'obj-%d' % k
            b['varname'] = 'Stage%d %s' % (i, kind)
            b['annotation'] = NOTE_TEXT[kind]
            b['patching_rect'] = [2400.0 + 60 * i, 20.0 * (k - 3000), 43.0, 13.0]
            b.pop('hidden', None)
            param(b, parameter_initial=[init], parameter_initial_enable=1, parameter_type=1, parameter_unitstyle=5,
                  parameter_mmin=float(lo), parameter_mmax=100.0, parameter_steps=0,
                  parameter_longname='Stage%d %s' % (i, kind),
                  parameter_shortname='St%d Prob' % i, parameter_invisible=0)
            add_box(P, b)
            ids[kind].append(b['id'])
        k += 2                                      # the free slot, then Slide
        t = clone(toggle_t, 'obj-%d' % k, [2400.0 + 60 * i, 20.0 * (k - 3000), 43.0, 13.0], None,
                  varname='Stage%d Slide' % i, text='Slide', texton='Slide', annotation=NOTE_TEXT['Slide'])
        param(t, parameter_enum=['off', 'on'], parameter_initial=[0], parameter_initial_enable=1,
              parameter_longname='Stage%d Slide' % i, parameter_shortname='St%d Slide' % i,
              parameter_mmin=0.0, parameter_mmax=1.0, parameter_invisible=0)
        add_box(P, t)
        ids['Slide'].append(t['id'])
    assert k == 3024
    return ids


STAGE_DIV = ['obj-66', 'obj-78', 'obj-80', 'obj-95', 'obj-105', 'obj-107', 'obj-109', 'obj-110']
STAGE_LEN = ['obj-35', 'obj-32', 'obj-11', 'obj-38', 'obj-46', 'obj-49', 'obj-52', 'obj-42']


def p2_engine(P):
    """The note path, all Max objects in the timing thread; ml185stage.js only
    builds the tables. Ids obj-3101..3199 (decisions, tables) and obj-3201..3240 (note output).

    Decisions: when the step grid plays a stage, before the note goes out, look up the stage's note
    length, slide flag and probability and roll it. A failed roll (or velocity 0) closes the gate in
    front of the scale snap, so neither the bend nor the note goes out. A stage switched off
    (StageN On) has probability 0 in the table. The script taps include the On toggles (prepend stage on N).

    Note output: a one-note handler replaces makenote (whose repeat mode cannot change while notes
    sound: setting it flushes them). It remembers the sounding note H, whether H was a sliding note,
    and one re-armable timer (the note length). For each new note p:
      no note sounding       -> note-on p
      H sounding, p != H     -> note-on p, note-off H 30 ms later (legato: a mono synth glides)
      H sounding, p == H     -> note-off H, then note-on p  (re-attack)
      ... unless H was a sliding note and p == H -> tie: no new attack, H keeps sounding
    then H = p and the timer restarts with p's length; when it runs out, H ends. Stop / device off end H.
    """
    R = boxes(P)
    assert R['obj-50']['varname'] == 'live.step[1]' and R['obj-100']['text'] == 'unpack 0 0 0 0. 0 0'
    assert R['obj-940']['text'] == 'clip 0 127' and R['obj-942']['text'] == 'zl.lookup'
    assert R['obj-82']['maxclass'] == 'number' and R['obj-44']['text'] == 'noteout'
    assert R['obj-1012']['text'] == 'r ---xstop' and R['obj-1013']['text'] == 'stop'
    X, Y = 2600.0, 600.0
    def o(bid, text, ins, outs, types, dx, dy, w=60.0):
        add_box(P, newobj(bid, text, ins, outs, types, [X + dx, Y + dy, w, 20.0]))
    def m(bid, text, dx, dy, w=60.0):
        add_box(P, message(bid, text, [X + dx, Y + dy, w, 20.0]))
    o('obj-3101', 'js ml185stage.js', 1, 7, [''] * 7, 0, 0, 200.0)
    o('obj-3102', 't l l', 1, 2, ['', ''], 0, 60)
    o('obj-3103', 'unpack 0 0 0', 1, 3, ['int', 'int', 'int'], 60, 90, 80.0)
    o('obj-3104', '- 1', 2, 1, ['int'], 60, 120, 30.0)
    o('obj-3105', 't i i i', 1, 3, ['int', 'int', 'int'], 60, 150)
    o('obj-3106', 'zl.lookup', 2, 2, ['', ''], 200, 180)             # note length (ticks)
    o('obj-3107', 'i', 2, 1, ['int'], 200, 210, 30.0)               # note length of this note
    o('obj-3108', 'zl.lookup', 2, 2, ['', ''], 130, 180)             # slide flag
    o('obj-3109', 'i', 2, 1, ['int'], 130, 210, 30.0)               # slide flag of this note
    o('obj-3110', 'zl.lookup', 2, 2, ['', ''], 60, 180)              # probability
    o('obj-3111', 't b i', 1, 2, ['bang', 'int'], 60, 210, 40.0)
    o('obj-3112', 'random 100', 2, 1, ['int'], 40, 240, 70.0)
    # random 100 gives 0..99 and the note passes when it is below the probability: 0 % never passes
    # (a stage switched off is sent 0), 100 % always does
    o('obj-3113', '<', 2, 1, ['int'], 60, 270, 30.0)
    o('obj-3114', 'gate 1 1', 2, 1, [''], 300, 330)                  # this note on / off
    o('obj-3115', '> 0', 2, 1, ['int'], 160, 120, 30.0)             # velocity 0 = no note
    o('obj-3116', '&&', 2, 1, ['int'], 60, 300, 30.0)

    remove_line(P, 'obj-50', 0, 'obj-100', 0)
    add_line(P, 'obj-50', 0, 'obj-3102', 0)
    add_line(P, 'obj-3102', 0, 'obj-100', 0)          # 2nd: the note itself
    add_line(P, 'obj-3102', 1, 'obj-3103', 0)         # 1st: decisions for this stage
    add_line(P, 'obj-3103', 2, 'obj-3115', 0)
    add_line(P, 'obj-3115', 0, 'obj-3116', 1)
    add_line(P, 'obj-3103', 0, 'obj-3104', 0)
    add_line(P, 'obj-3104', 0, 'obj-3105', 0)
    add_line(P, 'obj-3105', 2, 'obj-3106', 0)
    add_line(P, 'obj-3106', 0, 'obj-3107', 1)
    add_line(P, 'obj-3105', 1, 'obj-3108', 0)
    add_line(P, 'obj-3108', 0, 'obj-3109', 1)
    add_line(P, 'obj-3105', 0, 'obj-3110', 0)
    add_line(P, 'obj-3110', 0, 'obj-3111', 0)
    add_line(P, 'obj-3111', 1, 'obj-3113', 1)
    add_line(P, 'obj-3111', 0, 'obj-3112', 0)
    add_line(P, 'obj-3112', 0, 'obj-3113', 0)
    add_line(P, 'obj-3113', 0, 'obj-3116', 0)
    add_line(P, 'obj-3116', 0, 'obj-3114', 0)
    remove_line(P, 'obj-178', 0, 'obj-940', 0)
    add_line(P, 'obj-178', 0, 'obj-3114', 1)
    add_line(P, 'obj-3114', 0, 'obj-940', 0)
    add_line(P, 'obj-3101', 0, 'obj-3110', 1)
    add_line(P, 'obj-3101', 1, 'obj-3106', 1)
    add_line(P, 'obj-3101', 2, 'obj-3108', 1)

    # note output: makenote obj-88 is bypassed (kept, unused)
    remove_line(P, 'obj-942', 0, 'obj-88', 0)
    remove_line(P, 'obj-82', 0, 'obj-88', 1)
    remove_line(P, 'obj-280', 0, 'obj-88', 2)          # the old length (extra1 x 8 - 1 ticks) retired
    o('obj-3201', 't i i i', 1, 3, ['int', 'int', 'int'], 400, 400)  # new pitch p
    o('obj-3202', 'i', 2, 1, ['int'], 520, 430, 30.0)               # p, for the note-on
    o('obj-3203', 'expr ($i2 >= 0) * (1 + ($i1 == $i2) * (1 + $i3))', 3, 1, [''], 400, 460, 260.0)
    o('obj-3204', 'sel 0 1 2 3', 2, 5, ['bang'] * 4 + [''], 400, 490, 90.0)
    o('obj-3205', 't b b', 1, 2, ['bang', 'bang'], 470, 520, 40.0)   # other pitch: on, then off
    o('obj-3206', 't b b', 1, 2, ['bang', 'bang'], 540, 520, 40.0)   # same pitch: off, then on
    o('obj-3207', 'pack 0 0', 2, 1, [''], 520, 560)                   # note-on: p and velocity
    o('obj-3208', 'i -1', 2, 1, ['int'], 620, 560, 40.0)             # H, the sounding note
    o('obj-3209', 'sel -1', 2, 2, ['bang', ''], 620, 590, 40.0)
    m('obj-3210', '$1 0', 620, 620, 40.0)                            # note-off H
    o('obj-3211', 't b i', 1, 2, ['bang', 'int'], 300, 560, 40.0)    # after the decision: H = p
    o('obj-3212', 't b b', 1, 2, ['bang', 'bang'], 300, 590, 40.0)
    o('obj-3213', 't b i', 1, 2, ['bang', 'int'], 200, 620, 40.0)    # length -> timer
    m('obj-3214', '$1 ticks', 240, 650)
    o('obj-3215', 'delay 119 ticks', 2, 1, ['bang'], 200, 680, 100.0)
    o('obj-3216', 't b b', 1, 2, ['bang', 'bang'], 200, 710, 40.0)   # timer ran out: off H, H = -1
    m('obj-3217', '-1', 260, 740, 30.0)
    o('obj-3218', 't b b', 1, 2, ['bang', 'bang'], 700, 400, 40.0)   # stop / device off
    o('obj-3220', 'i -1', 2, 1, ['int'], 760, 560, 40.0)             # H again, for the late release
    o('obj-3221', 'sel -1', 2, 2, ['bang', ''], 760, 590, 40.0)
    m('obj-3222', '$1 0', 760, 620, 40.0)
    o('obj-3223', 'pipe 30', 2, 1, ['int'], 760, 650, 60.0)       # the pitch, 30 ms later
    for a_, ao, b_, bi in (
            ('obj-942', 0, 'obj-3201', 0),
            ('obj-3201', 2, 'obj-3202', 1),             # 1st: keep p
            ('obj-3201', 1, 'obj-3203', 0),             # 2nd: decide
            ('obj-3201', 0, 'obj-3211', 0),             # 3rd: H = p, restart the timer
            ('obj-3203', 0, 'obj-3204', 0),
            ('obj-3204', 0, 'obj-3202', 0),             # none sounding: on
            ('obj-3204', 1, 'obj-3205', 0),
            ('obj-3204', 2, 'obj-3206', 0),             # (3 = tie: nothing)
            ('obj-3205', 1, 'obj-3202', 0), ('obj-3205', 0, 'obj-3220', 0),   # legato: release H 30 ms later
            ('obj-3206', 1, 'obj-3208', 0), ('obj-3206', 0, 'obj-3202', 0),
            ('obj-3202', 0, 'obj-3207', 0),
            ('obj-82', 0, 'obj-3207', 1),               # velocity (arrives before the pitch)
            ('obj-3207', 0, 'obj-44', 0),
            ('obj-3208', 0, 'obj-3209', 0),
            ('obj-3209', 1, 'obj-3210', 0),
            ('obj-3210', 0, 'obj-44', 0),
            ('obj-3211', 1, 'obj-3208', 1), ('obj-3211', 1, 'obj-3203', 1), ('obj-3211', 1, 'obj-3220', 1),
            ('obj-3220', 0, 'obj-3221', 0), ('obj-3221', 1, 'obj-3223', 0), ('obj-3223', 0, 'obj-3222', 0),
            ('obj-3222', 0, 'obj-44', 0),
            ('obj-3211', 0, 'obj-3212', 0),
            ('obj-3212', 1, 'obj-3109', 0),             # this note's slide flag ...
            ('obj-3109', 0, 'obj-3203', 2),             # ... decides the tie for the next note
            ('obj-3212', 0, 'obj-3107', 0),             # this note's length
            ('obj-3107', 0, 'obj-3213', 0),
            ('obj-3213', 1, 'obj-3214', 0), ('obj-3214', 0, 'obj-3215', 1),
            ('obj-3213', 0, 'obj-3215', 0),
            ('obj-3215', 0, 'obj-3216', 0),
            ('obj-3216', 1, 'obj-3208', 0),
            ('obj-3216', 0, 'obj-3217', 0),
            ('obj-3217', 0, 'obj-3208', 1), ('obj-3217', 0, 'obj-3203', 1), ('obj-3217', 0, 'obj-3220', 1),
            ('obj-1012', 0, 'obj-3218', 0),
            ('obj-3218', 1, 'obj-3208', 0),
            ('obj-3218', 0, 'obj-3217', 0),
            ('obj-1013', 0, 'obj-3215', 0)):           # 'stop' cancels the timer
        add_line(P, a_, ao, b_, bi)
    # H starts at -1 in the expression too
    m('obj-3219', '-1', 760, 400, 30.0)
    # tables before the script has run: the old behaviour
    o('obj-3121', 'loadbang', 1, 1, ['bang'], 300, 0)
    o('obj-3122', 't b b b b', 1, 4, ['bang', 'bang', 'bang', 'bang'], 300, 30)
    for bid, text, dst, dx in (('obj-3123', ' '.join(['100'] * 8), 'obj-3110', 300),
                               ('obj-3124', ' '.join(['119'] * 8), 'obj-3106', 420),
                               ('obj-3125', ' '.join(['0'] * 8), 'obj-3108', 540)):
        add_box(P, message(bid, text, [X + dx, Y + 60, 110.0, 20.0]))
        add_line(P, bid, 0, dst, 1)
    add_line(P, 'obj-3121', 0, 'obj-3122', 0)
    add_line(P, 'obj-3122', 0, 'obj-3123', 0)
    add_line(P, 'obj-3122', 1, 'obj-3124', 0)
    add_line(P, 'obj-3122', 2, 'obj-3125', 0)
    add_line(P, 'obj-3122', 3, 'obj-3219', 0)
    add_line(P, 'obj-3219', 0, 'obj-3203', 1)

    # every stage control and the clock report to the script; after load it reads them all by name
    k = 3130
    def tap(src, text):
        nonlocal k
        k += 1
        bid = 'obj-%d' % k
        add_box(P, newobj(bid, text, 1, 1, [''], [X + 700 + 130 * ((k - 3131) // 30), Y + 20 * ((k - 3131) % 30), 125.0, 20.0]))
        add_line(P, src, 0, bid, 0)
        add_line(P, bid, 0, 'obj-3101', 0)
    B = boxes(P)
    assert [B[i]['varname'] for i in STAGE_DIV] == ['Stage%d Div' % n for n in range(1, 9)]
    assert [B[i]['varname'] for i in STAGE_LEN] == ['Stage%d Length' % n for n in range(1, 9)]
    for n in range(1, 9):
        tap(STAGE_MODE[n - 1], 'prepend stage mode %d' % n)
        tap(STAGE_DIV[n - 1], 'prepend stage div %d' % n)
        tap(STAGE_LEN[n - 1], 'prepend stage len %d' % n)
        for kind, word in (('Probability', 'prob'), ('Slide', 'slide'), ('On', 'on')):
            src = next(b['box']['id'] for b in P['boxes'] if b['box'].get('varname') == 'Stage%d %s' % (n, kind))
            tap(src, 'prepend stage %s %d' % (word, n))
    tap('obj-267', 'prepend clock')
    tap('obj-4560', 'prepend gate')                   # Gate: scales the note length table
    assert k < 3199
    add_box(P, message('obj-3199', 'refresh', [X + 300, Y + 100, 50.0, 20.0]))
    add_line(P, 'obj-922', 2, 'obj-3199', 0)          # after load, with the stage tabs
    add_line(P, 'obj-3199', 0, 'obj-3101', 0)


def p3_controls(P, tab_t, toggle_t):
    """Restart (Clock column), Key In and Key Mode (Scale tab). Ids obj-3301..3303."""
    b = clone(tab_t, 'obj-3301', [2400.0, 700.0, 78.0, 13.0], None, varname='Restart',
              annotation='Jumps back to stage 1 at the start of every 1st, 2nd, 4th or 8th bar (counted from bar 1 '
                         'of the song): the same clean restart as pressing Play (with Direction R: a new random '
                         'stage). Off: the sequence runs freely.')
    param(b, parameter_enum=['Off', '1', '2', '4', '8'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Restart', parameter_shortname='Restart', parameter_mmin=0.0, parameter_mmax=4.0,
          parameter_invisible=0)
    strip_colors(b)
    add_box(P, b)
    t = clone(toggle_t, 'obj-3302', [2400.0, 730.0, 54.0, 13.0], None, varname='Key In', text='On',
              texton='On',
              annotation='Transpose from notes coming into this track (a keyboard, or a clip on the same track): the '
                         'offset is the played note minus C3, applied from the next note. With a scale on, the riff '
                         'stays in key. While on, incoming notes only transpose; other MIDI still follows MIDI Thru.')
    param(t, parameter_enum=['off', 'on'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Key In', parameter_shortname='Key In', parameter_mmin=0.0, parameter_mmax=1.0,
          parameter_invisible=0)
    add_box(P, t)
    k = clone(tab_t, 'obj-3303', [2400.0, 760.0, 54.0, 13.0], None, varname='Key Mode',
              annotation='Latch: the last played note keeps the transpose. Hold: the transpose lasts while the key '
                         'is held and returns to 0 when it is released.')
    param(k, parameter_enum=['Latch', 'Hold'], parameter_initial=[0], parameter_initial_enable=1,
          parameter_longname='Key Mode', parameter_shortname='Key Mode', parameter_mmin=0.0, parameter_mmax=1.0,
          parameter_invisible=1)
    strip_colors(k)
    add_box(P, k)


def p3_engine(P):
    """R direction, Max objects in the timing thread. Ids obj-3310..3399 in the root,
    obj-910..921 inside p Transportation.

    Restart: the step grid takes its step from the song position it is sent (a reset only makes it
    repeat the current step), so the bar number in that time message wraps every N bars (bar 5 -> 1
    with N = 4): the grid starts its loop again on the bar. On that bar ---xrestart also restarts the
    stage logic (stage-change detector, stage counters, R picking), like a jump does.

    R in Stages mode: the tick grid only supplies the pulses. A counter plays the picked stage S for its
    Length, then picks a new one (random among the stages in use; repeats allowed; 'set 0' on the
    stage-change detector so a repeat restarts the stage). The cascade gets start(S) + position, so
    everything after it (stage counters, notes, lights) works unchanged.

    Key In: incoming note-ons set the transpose to note - 60 (Hold: a release of the last key sets 0);
    added to Transpose before the scale snap. While on, a byte filter keeps note-ons out of MIDI Thru
    (note-offs and other messages pass, so a note sounding when Key In turns on still ends)."""
    R = boxes(P)
    T = R['obj-294']['patcher']
    TB = boxes(T)
    assert TB['obj-28']['text'] == 'transport' and TB['obj-6']['text'] == 'buddy'
    assert R['obj-128']['text'] == 'unpack' and R['obj-113']['text'].startswith('if $i1<=$i2 then 1')
    assert R['obj-162']['text'] == 'change' and R['obj-281']['text'] == 'midiin' and R['obj-292']['text'] == 'gate'
    assert R['obj-178']['text'] == '+ 0' and R['obj-157']['varname'] == 'Length Mode'
    assert R['obj-1004']['text'] == 'set 0' and R['obj-1005']['text'] == '0'
    X, Y = 3400.0, 600.0
    def o(p, bid, text, ins, outs, types, dx, dy, w=60.0):
        add_box(p, newobj(bid, text, ins, outs, types, [X + dx, Y + dy, w, 20.0]))
    def m(p, bid, text, dx, dy, w=60.0):
        add_box(p, message(bid, text, [X + dx, Y + dy, w, 20.0]))
    def ln(p, *pairs):
        for a, ao, b, bi in pairs:
            add_line(p, a, ao, b, bi)

    # -- Restart every N bars ------------------------------------------------
    o(P, 'obj-3310', 's ---restart', 1, 0, [], 0, 0, 80.0)
    ln(P, ('obj-3301', 0, 'obj-3310', 0), ('obj-922', 3, 'obj-3301', 0))
    def ot(bid, text, ins, outs, types, x, y, w=60.0):
        add_box(T, newobj(bid, text, ins, outs, types, [x, y, w, 20.0]))
    ot('obj-910', 'r ---restart', 0, 1, [''], 600.0, 430.0, 80.0)
    ot('obj-911', 'zl.lookup', 2, 2, ['', ''], 600.0, 460.0)
    ot('obj-912', 'loadbang', 1, 1, ['bang'], 700.0, 430.0)
    add_box(T, message('obj-913', '0 1 2 4 8', [700.0, 460.0, 70.0, 20.0]))
    ot('obj-914', 't i i', 1, 2, ['int', 'int'], 450.0, 430.0, 40.0)
    ot('obj-915', 'change -1', 1, 3, ['', 'int', 'int'], 500.0, 490.0)
    ot('obj-916', '- 1', 2, 1, ['int'], 500.0, 520.0, 30.0)
    ot('obj-917', 'expr ($i2 > 0) * (($i1 % ($i2 + ($i2 == 0))) == 0)', 2, 1, [''], 500.0, 550.0, 260.0)
    ot('obj-918', 'sel 1', 2, 2, ['bang', ''], 500.0, 580.0, 40.0)
    ot('obj-919', 's ---xrestart', 1, 0, [], 500.0, 610.0, 80.0)
    ot('obj-920', 'expr $i1 - ($i2 > 0) * (($i1 - 1) - (($i1 - 1) % ($i2 + ($i2 == 0))))', 2, 1, [''], 300.0, 460.0, 330.0)
    remove_line(T, 'obj-28', 0, 'obj-6', 0)
    ln(T, ('obj-28', 0, 'obj-914', 0),
       ('obj-914', 1, 'obj-915', 0),        # 1st: a bar to restart on? (before the time message)
       ('obj-914', 0, 'obj-920', 0),        # 2nd: bar number wrapped every N bars -> time message
       ('obj-920', 0, 'obj-6', 0),
       ('obj-915', 0, 'obj-916', 0), ('obj-916', 0, 'obj-917', 0), ('obj-917', 0, 'obj-918', 0),
       ('obj-918', 0, 'obj-919', 0),
       ('obj-910', 0, 'obj-911', 0), ('obj-911', 0, 'obj-917', 1), ('obj-911', 0, 'obj-920', 1),
       ('obj-912', 0, 'obj-913', 0), ('obj-913', 0, 'obj-911', 1))

    # -- R: whole stages ------------------------------------------------------
    o(P, 'obj-3320', 'gate 2 1', 2, 2, ['', ''], 0, 60)
    o(P, 'obj-3321', 't b b', 1, 2, ['bang', 'bang'], 100, 90, 40.0)
    o(P, 'obj-3322', 'i', 2, 1, ['int'], 100, 120, 30.0)                  # position in the stage
    o(P, 'obj-3319', 'i', 2, 1, ['int'], 40, 400, 30.0)                   # same, read for the output
    o(P, 'obj-3323', '+ 1', 2, 1, ['int'], 100, 150, 30.0)
    o(P, 'obj-3324', 't i i', 1, 2, ['int', 'int'], 100, 180, 40.0)
    o(P, 'obj-3325', 'i', 2, 1, ['int'], 160, 210, 30.0)                  # next position
    o(P, 'obj-3326', '>', 2, 1, ['int'], 100, 210, 30.0)                  # past the stage's Length?
    o(P, 'obj-3327', 'sel 1', 2, 2, ['bang', ''], 100, 240, 40.0)
    o(P, 'obj-3328', 't b b b', 1, 3, ['bang', 'bang', 'bang'], 40, 270, 50.0)   # pick a new stage
    m(P, 'obj-3329', 'set 0', 140, 300, 40.0)
    o(P, 'obj-3330', 'random 8', 2, 1, ['int'], 80, 300, 60.0)
    o(P, 'obj-3331', 't i i', 1, 2, ['int', 'int'], 80, 330, 40.0)
    o(P, 'obj-3332', 'zl.lookup', 2, 2, ['', ''], 140, 360)              # its Length
    o(P, 'obj-3333', 'zl.lookup', 2, 2, ['', ''], 60, 360)               # steps before it
    m(P, 'obj-3334', '1', 20, 300, 30.0)
    o(P, 'obj-3335', 't b', 1, 1, ['bang'], 160, 270, 30.0)
    o(P, 'obj-3336', '+ 0', 2, 1, ['int'], 100, 400, 30.0)                # start(S) + position
    m(P, 'obj-3337', '0', 200, 120, 30.0)                                 # restart the picking
    o(P, 'obj-3338', 'r ---xreset', 0, 1, [''], 200, 90, 75.0)
    o(P, 'obj-3311', 'r ---xrestart', 0, 1, [''], 0, 30, 80.0)
    o(P, 'obj-3312', 't b b b', 1, 3, ['bang', 'bang', 'bang'], 100, 30, 50.0)
    o(P, 'obj-3339', 'route rgate rstages rreset keyin keymode', 1, 6, [''] * 6, 300, 0, 230.0)
    remove_line(P, 'obj-128', 0, 'obj-113', 0)
    ln(P, ('obj-128', 0, 'obj-3320', 1),
       ('obj-3320', 0, 'obj-113', 0),       # normal: the grid step
       ('obj-3320', 1, 'obj-3321', 0),      # R: a pulse
       ('obj-3321', 1, 'obj-3322', 0), ('obj-3322', 0, 'obj-3323', 0), ('obj-3323', 0, 'obj-3324', 0),
       ('obj-3324', 1, 'obj-3325', 1), ('obj-3324', 0, 'obj-3326', 0), ('obj-3326', 0, 'obj-3327', 0),
       ('obj-3327', 0, 'obj-3328', 0),
       ('obj-3328', 2, 'obj-3329', 0), ('obj-3329', 0, 'obj-162', 0),
       ('obj-3328', 1, 'obj-3330', 0), ('obj-3330', 0, 'obj-3331', 0),
       ('obj-3331', 1, 'obj-3332', 0), ('obj-3332', 0, 'obj-3326', 1),
       ('obj-3331', 0, 'obj-3333', 0), ('obj-3333', 0, 'obj-3336', 1),
       ('obj-3328', 0, 'obj-3334', 0), ('obj-3334', 0, 'obj-3322', 1),
       ('obj-3327', 1, 'obj-3335', 0), ('obj-3335', 0, 'obj-3325', 0), ('obj-3325', 0, 'obj-3322', 1),
       ('obj-3321', 0, 'obj-3319', 0), ('obj-3319', 0, 'obj-3336', 0), ('obj-3336', 0, 'obj-113', 0),
       ('obj-3334', 0, 'obj-3319', 1), ('obj-3325', 0, 'obj-3319', 1),     # read-out copy of the position
       ('obj-3311', 0, 'obj-3312', 0),
       ('obj-3312', 2, 'obj-1004', 0),      # 'set 0': the next stage counts as a new stage
       ('obj-3312', 1, 'obj-1005', 0),      # '0': stage counters back to the start
       ('obj-3312', 0, 'obj-3337', 0),      # R: pick again
       ('obj-3338', 0, 'obj-3337', 0), ('obj-3337', 0, 'obj-3326', 1), ('obj-3337', 0, 'obj-3322', 1),
       ('obj-3337', 0, 'obj-3319', 1),
       ('obj-3101', 3, 'obj-3332', 1), ('obj-3101', 4, 'obj-3333', 1),
       ('obj-3101', 5, 'obj-3339', 0),
       ('obj-3339', 0, 'obj-3320', 0), ('obj-3339', 1, 'obj-3330', 1), ('obj-3339', 2, 'obj-3337', 0))
    k = 3340
    for src, word in (('obj-73', 'direction'), ('obj-157', None), ('obj-72', 'seqlen')):
        k += 1
        if word is None:                            # Length Mode (Stages / Fixed) removed: id kept free
            continue
        o(P, 'obj-%d' % k, 'prepend ' + word, 1, 1, [''], 600, 30 * (k - 3340), 110.0)
        ln(P, (src, 0, 'obj-%d' % k, 0), ('obj-%d' % k, 0, 'obj-3101', 0))

    # -- Key In ----------------------------------------------------------------
    o(P, 'obj-3350', 't i i', 1, 2, ['int', 'int'], 0, 500, 40.0)
    o(P, 'obj-3351', 't i i', 1, 2, ['int', 'int'], 0, 530, 40.0)
    o(P, 'obj-3352', 'split 128 255', 3, 2, ['int', 'int'], 60, 560, 80.0)
    o(P, 'obj-3353', 'split 144 159', 3, 2, ['int', 'int'], 60, 590, 80.0)     # note-on statuses
    m(P, 'obj-3354', '1', 60, 620, 30.0)
    m(P, 'obj-3355', '0', 120, 620, 30.0)
    o(P, 'obj-3356', 'pak 0 0', 2, 1, [''], 60, 650)
    o(P, 'obj-3357', 'expr !($i1 && $i2)', 2, 1, [''], 60, 680, 110.0)
    o(P, 'obj-3358', 'gate 1 1', 2, 1, [''], 0, 710)
    o(P, 'obj-3360', 'midiparse', 1, 8, [''] * 8, 200, 530, 70.0)
    o(P, 'obj-3361', 'unpack 0 0', 1, 2, ['int', 'int'], 200, 560, 70.0)
    o(P, 'obj-3362', '> 0', 2, 1, ['int'], 260, 590, 30.0)
    o(P, 'obj-3363', '+ 1', 2, 1, ['int'], 260, 620, 30.0)
    o(P, 'obj-3364', 'gate 2', 2, 2, ['', ''], 200, 650)
    o(P, 'obj-3365', 't i i', 1, 2, ['int', 'int'], 260, 680, 40.0)
    o(P, 'obj-3366', '==', 2, 1, ['int'], 200, 710, 30.0)
    o(P, 'obj-3367', '- 60', 2, 1, ['int'], 260, 710, 40.0)
    o(P, 'obj-3368', '&&', 2, 1, ['int'], 200, 740, 30.0)
    o(P, 'obj-3369', 'sel 1', 2, 2, ['bang', ''], 200, 770, 40.0)
    m(P, 'obj-3370', '0', 200, 800, 30.0)
    o(P, 'obj-3371', 'gate 1 0', 2, 1, [''], 260, 830)
    o(P, 'obj-3372', 't b i', 1, 2, ['bang', 'int'], 260, 860, 40.0)
    o(P, 'obj-3373', '+ 0', 2, 1, ['int'], 260, 890, 30.0)                # Transpose + key offset
    o(P, 'obj-3374', 't b i i', 1, 3, ['bang', 'int', 'int'], 400, 500, 50.0)   # Key In on / off
    m(P, 'obj-3375', '0', 400, 530, 30.0)
    remove_line(P, 'obj-281', 0, 'obj-292', 1)
    remove_line(P, 'obj-18', 0, 'obj-178', 1)
    ln(P, ('obj-281', 0, 'obj-3350', 0),
       ('obj-3350', 1, 'obj-3351', 0),      # thru, notes filtered while Key In is on
       ('obj-3351', 1, 'obj-3352', 0), ('obj-3352', 0, 'obj-3353', 0),
       ('obj-3353', 0, 'obj-3354', 0), ('obj-3353', 1, 'obj-3355', 0),
       ('obj-3354', 0, 'obj-3356', 0), ('obj-3355', 0, 'obj-3356', 0),
       ('obj-3356', 0, 'obj-3357', 0), ('obj-3357', 0, 'obj-3358', 0),
       ('obj-3351', 0, 'obj-3358', 1), ('obj-3358', 0, 'obj-292', 1),
       ('obj-3350', 0, 'obj-3360', 0),      # key offset
       ('obj-3360', 0, 'obj-3361', 0),
       ('obj-3361', 1, 'obj-3362', 0), ('obj-3362', 0, 'obj-3363', 0), ('obj-3363', 0, 'obj-3364', 0),
       ('obj-3361', 0, 'obj-3364', 1),
       ('obj-3364', 1, 'obj-3365', 0), ('obj-3365', 1, 'obj-3366', 1), ('obj-3365', 0, 'obj-3367', 0),
       ('obj-3367', 0, 'obj-3371', 1),
       ('obj-3364', 0, 'obj-3366', 0), ('obj-3366', 0, 'obj-3368', 0), ('obj-3368', 0, 'obj-3369', 0),
       ('obj-3369', 0, 'obj-3370', 0), ('obj-3370', 0, 'obj-3371', 1),
       ('obj-3371', 0, 'obj-3372', 0), ('obj-3372', 1, 'obj-3373', 1), ('obj-3372', 0, 'obj-3373', 0),
       ('obj-18', 0, 'obj-3373', 0), ('obj-3373', 0, 'obj-178', 1),
       ('obj-3302', 0, 'obj-3374', 0), ('obj-3339', 3, 'obj-3374', 0),
       ('obj-3374', 2, 'obj-3356', 1), ('obj-3374', 1, 'obj-3371', 0),
       ('obj-3374', 0, 'obj-3375', 0), ('obj-3375', 0, 'obj-3372', 0),
       ('obj-3303', 0, 'obj-3368', 1), ('obj-3339', 4, 'obj-3368', 1),
       ('obj-3339', 3, 'obj-4109', 0), ('obj-3339', 4, 'obj-4109', 1))


def key_in_switch(P, tab_t):
    """Left column: Key In as one switch Off / Latch / Hold. A hidden-parameter tab that
    writes the two stored parameters, Key In (on / off) and Key Mode (Latch / Hold), and shows them back
    (after load the stage script re-sends both). Ids obj-4101..4112."""
    ks = clone(tab_t, 'obj-4101', [3300.0, 900.0, 120.0, 13.0], None, varname='Key In Select',
               annotation='Transpose from notes coming into this track (keyboard or a clip on the same track): '
                          'Off; Latch (the last played note keeps the transpose); Hold (the transpose lasts '
                          'while the key is held). The offset is the played note minus C3; with a scale on the '
                          'riff stays in key.')
    param(ks, parameter_enum=['Off', 'Latch', 'Hold'], parameter_initial=[0], parameter_initial_enable=0,
          parameter_longname='Key In Select', parameter_shortname='Key In Sel', parameter_mmin=0.0,
          parameter_mmax=2.0, parameter_invisible=2)
    strip_colors(ks)
    add_box(P, ks)
    X, Y = 3300.0, 930.0
    add_box(P, newobj('obj-4102', 'sel 0 1 2', 4, 4, ['bang', 'bang', 'bang', ''], [X, Y, 70.0, 20.0]))
    add_box(P, message('obj-4103', '0', [X, Y + 30, 30.0, 20.0]))           # Key In off
    add_box(P, newobj('obj-4104', 't b b', 1, 2, ['bang', 'bang'], [X + 40, Y + 30, 40.0, 20.0]))
    add_box(P, newobj('obj-4105', 't b b', 1, 2, ['bang', 'bang'], [X + 90, Y + 30, 40.0, 20.0]))
    add_box(P, message('obj-4106', '1', [X, Y + 60, 30.0, 20.0]))           # Key In on
    add_box(P, message('obj-4107', '0', [X + 40, Y + 60, 30.0, 20.0]))      # Latch
    add_box(P, message('obj-4108', '1', [X + 90, Y + 60, 30.0, 20.0]))      # Hold
    add_box(P, newobj('obj-4109', 'pak 0 0', 2, 1, [''], [X, Y + 100, 50.0, 20.0]))
    add_box(P, newobj('obj-4110', 'expr $i1 * (1 + $i2)', 2, 1, [''], [X, Y + 130, 120.0, 20.0]))
    add_box(P, newobj('obj-4111', 'prepend set', 1, 1, [''], [X, Y + 160, 80.0, 20.0]))
    for a_, ao, b_, bi in (('obj-4101', 0, 'obj-4102', 0),
                           ('obj-4102', 0, 'obj-4103', 0), ('obj-4103', 0, 'obj-3302', 0),
                           ('obj-4102', 1, 'obj-4104', 0), ('obj-4104', 1, 'obj-4107', 0), ('obj-4104', 0, 'obj-4106', 0),
                           ('obj-4102', 2, 'obj-4105', 0), ('obj-4105', 1, 'obj-4108', 0), ('obj-4105', 0, 'obj-4106', 0),
                           ('obj-4107', 0, 'obj-3303', 0), ('obj-4108', 0, 'obj-3303', 0), ('obj-4106', 0, 'obj-3302', 0),
                           ('obj-3302', 0, 'obj-4109', 0), ('obj-3303', 0, 'obj-4109', 1),
                           ('obj-4109', 0, 'obj-4110', 0), ('obj-4110', 0, 'obj-4111', 0), ('obj-4111', 0, 'obj-4101', 0)):
        add_line(P, a_, ao, b_, bi)
    return 'obj-4101'


def p4_controls(P, button_t, tab_t, toggle_t):
    """Mutate on both pages: Mutate (once) | Auto | every 4 / 8 / 16 bars, the Sequencer
    page's for the ticked stage parts, the Mod page's for the lane knobs, independent. ml185scale.js picks
    the stage and counts the bars. Ids obj-3401..3408 (Sequencer page), obj-4700..4711 (Mod page, bar end).

    Bar end: p Transportation polls the transport every 10 ticks; on the first poll inside a bar's last
    16th, "barend <bar>" goes to the script once (any time signature: beats count in the denominator's
    unit, 1920 ticks to a whole note), so the next bar starts with the change in place."""
    def row(ids, prefix, long, short, msg, X, Y):
        bid, rb, s1, mm, tg, pa, tb, pe = ids
        b = clone(button_t, bid, [X, Y, 46.0, 13.0], None, varname=long, text='Mutate', texton='Mutate',
                  fontsize=9.0, annotation='')
        param(b, parameter_enum=['val1', 'val2'], parameter_longname=long, parameter_shortname=short,
              parameter_invisible=2, parameter_initial_enable=0)
        add_box(P, b)
        add_box(P, newobj(rb, 'route bang', 2, 2, ['', ''], [X, Y + 30, 60.0, 20.0]))
        add_box(P, newobj(s1, 'sel 1', 2, 2, ['bang', ''], [X + 70, Y + 60, 40.0, 20.0]))
        add_box(P, message(mm, msg, [X, Y + 90, 70.0, 20.0]))
        t = clone(toggle_t, tg, [X + 120, Y, 30.0, 13.0], None, varname=long + ' Auto', text='Auto', texton='Auto',
                  fontsize=9.0, annotation='')
        param(t, parameter_enum=['off', 'on'], parameter_initial=[0], parameter_initial_enable=1,
              parameter_longname=long + ' Auto', parameter_shortname=prefix + 'Auto', parameter_mmin=0.0,
              parameter_mmax=1.0, parameter_invisible=0)
        add_box(P, t)
        add_box(P, newobj(pa, 'prepend %sauto' % msg, 1, 1, [''], [X + 120, Y + 30, 140.0, 20.0]))
        e = clone(tab_t, tb, [X + 280, Y, 54.0, 13.0], None, varname=long + ' Every', fontsize=9.0, annotation='',
                  active=0)                 # grey while Auto is off (the default); the script keeps it in step
        param(e, parameter_enum=['4', '8', '16'], parameter_initial=[0], parameter_initial_enable=1,
              parameter_longname=long + ' Every', parameter_shortname=prefix + 'Every', parameter_mmin=0.0,
              parameter_mmax=2.0, parameter_invisible=0)
        strip_colors(e)
        add_box(P, e)
        add_box(P, newobj(pe, 'prepend %severy' % msg, 1, 1, [''], [X + 280, Y + 30, 140.0, 20.0]))
        for a_, ao, b_, bi in ((bid, 0, rb, 0), (rb, 0, mm, 0), (rb, 1, s1, 0), (s1, 0, mm, 0), (mm, 0, 'obj-930', 0),
                               (tg, 0, pa, 0), (pa, 0, 'obj-930', 0), (tb, 0, pe, 0), (pe, 0, 'obj-930', 0)):
            add_line(P, a_, ao, b_, bi)
    row(('obj-3401', 'obj-3403', 'obj-3404', 'obj-3405', 'obj-3402', 'obj-3406', 'obj-3407', 'obj-3408'),
        'Mut ', 'Mutate', 'Mutate', 'mutate', 2400.0, 800.0)
    row(('obj-4700', 'obj-4701', 'obj-4702', 'obj-4703', 'obj-4704', 'obj-4705', 'obj-4706', 'obj-4707'),
        'Lane ', 'Lane Mutate', 'Lane Mut', 'lanemutate', 2400.0, 950.0)
    # bar end, measured in the poll that drives the step grid
    T = boxes(P)['obj-294']['patcher']
    TB = boxes(T)
    assert TB['obj-28']['text'] == 'transport' and TB['obj-1']['text'].startswith('metro @interval 10 ticks')
    assert not any(k in TB for k in ('obj-930', 'obj-931', 'obj-932', 'obj-933', 'obj-934'))
    add_box(T, newobj('obj-930', 'unpack 0 0', 1, 2, ['int', 'int'], [800.0, 430.0, 70.0, 20.0]))
    add_box(T, newobj('obj-931', 'expr $i1 * ((($i2 - 1) * 1920 / $i5 + $i3) >= ($i4 * 1920 / $i5 - 120))', 5, 1,
                      [''], [800.0, 460.0, 420.0, 20.0]))
    add_box(T, newobj('obj-932', 'change', 1, 3, ['', 'int', 'int'], [800.0, 490.0, 50.0, 20.0]))
    add_box(T, newobj('obj-933', 'sel 0', 2, 2, ['bang', ''], [800.0, 520.0, 40.0, 20.0]))
    add_box(T, newobj('obj-934', 's ---barend', 1, 0, [], [840.0, 550.0, 75.0, 20.0]))
    for a_, ao, b_, bi in (('obj-28', 5, 'obj-930', 0), ('obj-930', 0, 'obj-931', 3), ('obj-930', 1, 'obj-931', 4),
                           ('obj-28', 2, 'obj-931', 2), ('obj-28', 1, 'obj-931', 1), ('obj-28', 0, 'obj-931', 0),
                           ('obj-931', 0, 'obj-932', 0), ('obj-932', 0, 'obj-933', 0), ('obj-933', 1, 'obj-934', 0)):
        add_line(T, a_, ao, b_, bi)
    add_box(P, newobj('obj-4710', 'r ---barend', 0, 1, [''], [2400.0, 1100.0, 75.0, 20.0]))
    add_box(P, newobj('obj-4711', 'prepend barend', 1, 1, [''], [2400.0, 1130.0, 95.0, 20.0]))
    add_line(P, 'obj-4710', 0, 'obj-4711', 0)
    add_line(P, 'obj-4711', 0, 'obj-930', 0)


LANES = 'ABC'        # mod lanes (three, not four)
LANE_TEXT = ('Value this stage sends to the parameter mapped on lane %s (Map, on the right), between the '
             'lane\'s Min and Max, each time the stage starts.')
# Ableton's own Map button (liveui.map): LCD look in the theme's LCD colours
MAP_COLORS = {'activebgcolor': 'live_lcd_bg', 'activebgoncolor': 'live_lcd_bg', 'bgcolor': 'live_lcd_bg',
              'bgoncolor': 'live_lcd_bg', 'activetextcolor': 'live_lcd_control_fg',
              'activetextoncolor': 'live_lcd_control_fg', 'bordercolor': 'live_lcd_control_fg',
              'textcolor': 'live_lcd_control_fg_zombie', 'textoffcolor': 'live_lcd_control_fg_zombie'}


def p5_controls(P, numbox_t, button_t, toggle_t, dial_t):
    """Mod lane parameters: Mod A-C Stage1-8 (Mod page), Mod A-C Min / Max / Smooth and the
    Map / unmap buttons (right panel on the Mod page). Ids obj-3501..3524 lane values, 3541.. Map,
    3545.. unmap, 3551.. Min, 3555.. Max, 3559.. Smooth. Returns the ids by part."""
    ids = {'stage': [[], [], []], 'map': [], 'unmap': [], 'min': [], 'max': [], 'smooth': []}
    def numbox(bid, long, short, lo, hi, init, unit, text, invisible, y):
        b = copy.deepcopy(numbox_t)
        b.update(id=bid, varname=long, annotation=text, patching_rect=[2600.0, y, 43.0, 13.0])
        b.pop('hidden', None)
        param(b, parameter_initial=[init], parameter_initial_enable=1, parameter_type=0, parameter_unitstyle=unit,
              parameter_mmin=float(lo), parameter_mmax=float(hi), parameter_steps=0, parameter_longname=long,
              parameter_shortname=short, parameter_invisible=invisible)
        add_box(P, b)
        return bid
    for l, letter in enumerate(LANES):
        for i in range(1, 9):                     # a knob per stage (Mod page)
            d = copy.deepcopy(dial_t)
            bid = 'obj-%d' % (3500 + 8 * l + i)
            # triangle 0: Live draws a knob with the top triangle as bipolar (arc from the middle)
            d.update(id=bid, varname='Mod %s Stage%d' % (letter, i), annotation=LANE_TEXT % letter,
                     patching_rect=[2600.0, 900.0 + 40 * (8 * l + i), 49.0, 36.0], triangle=0)
            param(d, parameter_initial=[0.0], parameter_initial_enable=1, parameter_type=0, parameter_unitstyle=5,
                  parameter_mmin=0.0, parameter_mmax=100.0, parameter_steps=0,
                  parameter_longname='Mod %s Stage%d' % (letter, i), parameter_shortname='%s St%d' % (letter, i),
                  parameter_invisible=0)
            add_box(P, d)
            ids['stage'][l].append(bid)
        m = clone(toggle_t, 'obj-%d' % (3541 + l), [2700.0, 900.0 + 30 * l, 117.0, 15.0], None,
                  varname='Mod %s Map' % letter, text='Map', texton='Map', parameter_enable=0, appearance=2,
                  fontsize=9.0,
                  annotation='Click, then click any parameter in Live to map lane %s to it (Map blinks while it '
                             'waits; click again to cancel). The lane\'s knob row then moves that parameter each '
                             'time a stage starts. Shows the mapped parameter\'s name; X unmaps. The mapping is '
                             'saved with the set. A parameter that is already remote-controlled (by another lane or '
                             'device) is refused and the button shows Map again.' % letter)
        m['saved_attribute_attributes'] = {k: {'expression': 'themecolor.' + v} for k, v in MAP_COLORS.items()}
        add_box(P, m)
        ids['map'].append(m['id'])
        u = clone(toggle_t, 'obj-%d' % (3545 + l), [2810.0, 900.0 + 30 * l, 15.0, 15.0], None,
                  varname='Mod %s Unmap' % letter, text='X', texton='X', parameter_enable=0, appearance=2,
                  fontsize=9.0, annotation='Removes lane %s\'s mapping: the parameter is free again.' % letter)
        u['saved_attribute_attributes'] = {k: {'expression': 'themecolor.' + v} for k, v in MAP_COLORS.items()}
        add_box(P, u)
        ids['unmap'].append(u['id'])
        ids['min'].append(numbox('obj-%d' % (3551 + l), 'Mod %s Min' % letter, '%s Min' % letter, 0, 100, 0, 5,
                                 'Lowest point of the mapped parameter\'s range that lane %s reaches (stage value 0 %%).'
                                 % letter, 1, 1200.0 + 20 * l))
        ids['max'].append(numbox('obj-%d' % (3555 + l), 'Mod %s Max' % letter, '%s Max' % letter, 0, 100, 100, 5,
                                 'Highest point of the mapped parameter\'s range that lane %s reaches (stage value '
                                 '100 %%). Set Max below Min to turn the lane upside down.' % letter, 1,
                                 1300.0 + 20 * l))
        ids['smooth'].append(numbox('obj-%d' % (3559 + l), 'Mod %s Smooth' % letter, '%s Smooth' % letter, 0, 2000,
                                    0, 2, 'Smoothing for lane %s: 0 ms jumps to each stage\'s value; longer times '
                                    'ease into it.' % letter, 1, 1400.0 + 20 * l))
    return ids


def p5_engine(P):
    """The mod lanes. When a stage starts (the stage-change detector's
    ---chtrig, also on R picks and restarts), each lane looks up the stage's value (0..1, Min / Max
    already applied by ml185stage.js), scales it to the target's own range (min + x * (max - min), an
    expr in the timing thread) and sends it with the lane's Smooth time to live.remote~: no undo
    entries, no automation written. live.map (strict: not this device) picks the target; both keep it
    with the set (persistent mapping). After load the button shows the target's name again. A target
    live.remote~ refuses (already remote-controlled, "mapping 0") is unmapped, so the button never
    shows a lane that does nothing.
    Range (no @normalized, which needs Max 8.6): whenever a target id arrives (Map, or getid after
    load) a gate in front of the pack closes; for an id other than 0 a second live.object is given it
    and asked "get min", then "get max"; the max reply opens the gate. So nothing is sent before the
    target's range is known.
    Ids obj-3570..3699 (scaling: 3660 + 12 * lane + 0..11), 3800..3829, 4300..4383 (Map look)."""
    R = boxes(P)
    assert R['obj-151']['text'] == 's ---chtrig'
    X, Y = 4200.0, 600.0
    def o(bid, text, ins, outs, types, dx, dy, w=60.0, **kw):
        b = newobj(bid, text, ins, outs, types, [X + dx, Y + dy, w, 20.0])
        b.update(kw)
        add_box(P, b)
    def m(bid, text, dx, dy, w=60.0):
        add_box(P, message(bid, text, [X + dx, Y + dy, w, 20.0]))
    def ln(*pairs):
        for a, ao, b, bi in pairs:
            add_line(P, a, ao, b, bi)
    persist = {'saved_object_attributes': {'_persistence': 1}}
    o('obj-3570', 'r ---chtrig', 0, 1, [''], 0, 0, 70.0)
    o('obj-3571', 'sel 0', 2, 2, ['bang', ''], 0, 30, 40.0)
    o('obj-3572', '- 1', 2, 1, ['int'], 0, 60, 30.0)
    n_l = len(LANES)
    o('obj-3573', 't ' + ' '.join(['i'] * n_l), 1, n_l, ['int'] * n_l, 0, 90, 70.0)
    o('obj-3586', 'route ' + ' '.join(['mod' + c.lower() for c in LANES] + ['glide' + c.lower() for c in LANES]),
      1, 2 * n_l + 1, [''] * (2 * n_l + 1), 300, 0, 330.0)
    ln(('obj-3570', 0, 'obj-3571', 0), ('obj-3571', 1, 'obj-3572', 0), ('obj-3572', 0, 'obj-3573', 0),
       ('obj-3101', 6, 'obj-3586', 0))
    for l, letter in enumerate(LANES):
        dx = 160 * l
        zl, pk, lr = 'obj-%d' % (3574 + l), 'obj-%d' % (3578 + l), 'obj-%d' % (3582 + l)
        o(zl, 'zl.lookup', 2, 2, ['', ''], dx, 130)
        o(pk, 'pack 0. 0.', 2, 1, [''], dx, 160)
        o(lr, 'live.remote~', 2, 1, [''], dx, 190, 150.0, **copy.deepcopy(persist))
        lm = 'obj-%d' % (3587 + l)
        o(lm, 'live.map @strict 1', 1, 5, ['', '', '', 'int', ''], dx, 250, 90.0, **copy.deepcopy(persist))
        b = 3600 + 20 * l                      # per lane 3600..3619, 3620..3639, ...
        mp, ub, rb, s1, um = ('obj-%d' % (b + k) for k in range(5))
        m(mp, 'mapping $1', dx, 220, 70.0)
        o(ub, 'route bang', 2, 2, ['', ''], dx + 80, 220)
        o(s1, 'sel 1', 2, 2, ['bang', ''], dx + 80, 240, 40.0)
        m(um, 'unmap', dx + 80, 260, 40.0)
        m('obj-%d' % (4450 + l), 'set 0', dx + 130, 260, 40.0)
        st = 'obj-%d' % (b + 5)
        m(st, 'set $1', dx, 280, 45.0)
        sn, ts = 'obj-%d' % (b + 6), 'obj-%d' % (b + 7)
        o(sn, 'route <none>', 2, 2, ['', ''], dx, 310, 80.0)
        o(ts, 'tosymbol', 1, 1, [''], dx, 340)
        sp6, pt6, nm = ('obj-%d' % (3800 + 10 * l + k) for k in range(6, 9))
        o(sp6, 'sprintf symout %.14s', 1, 1, [''], dx + 130, 370, 110.0)     # what fits the 82 px button
        o(pt6, 'prepend text', 1, 1, [''], dx + 130, 400, 80.0)
        m(nm, 'text Map', dx + 180, 340, 60.0)
        gi, ri, sz, ti, pi = ('obj-%d' % (b + k) for k in range(11, 16))
        m(gi, 'getid', dx, 440, 40.0)
        o(ri, 'route id mapping', 3, 3, ['', '', ''], dx, 470, 100.0)
        ms, mg, tl, tr, nz, rs = ('obj-%d' % (3800 + 10 * l + k) for k in range(6))
        o(ms, 'sel 0', 2, 2, ['bang', ''], dx + 110, 500, 40.0)
        o(mg, 'gate 1', 2, 1, [''], dx + 110, 530, 40.0)
        o(tl, 't l l', 1, 2, ['', ''], dx + 60, 280, 40.0)
        o(tr, 'route id', 2, 2, ['', ''], dx + 110, 280)
        o(nz, '!= 0', 2, 1, ['int'], dx + 110, 310, 40.0)
        o(rs, 'route symbol', 2, 2, ['', ''], dx + 90, 310, 80.0)
        o(sz, 'sel 0', 2, 2, ['bang', ''], dx, 500, 40.0)
        o(ti, 't b i', 1, 2, ['bang', 'int'], dx, 530, 40.0)
        o(pi, 'prepend id', 1, 1, [''], dx + 50, 560, 70.0)
        gn, lo, rn = ('obj-%d' % (b + k) for k in range(16, 19))
        m(gn, 'get name', dx, 560, 55.0)
        o(lo, 'live.object', 2, 1, [''], dx, 590, 70.0)
        o(rn, 'route name', 2, 2, ['', ''], dx, 620, 70.0)
        # the target's range: lane value 0..1 -> expr -> gate (open once max is known) -> pack
        (ex, gt, tz, nz0, tq, pq, gmin, gmax, lmm, rmm, tbf, op) = ('obj-%d' % (3660 + 12 * l + k) for k in range(12))
        o(ex, 'expr $f1 * ($f3 - $f2) + $f2', 3, 1, [''], dx + 70, 130, 150.0)
        o(gt, 'gate 1', 2, 1, [''], dx + 70, 160, 40.0)          # closed until max arrives
        o(tz, 't i 0', 1, 2, ['int', 'int'], dx + 240, 470, 40.0)   # a target id: close first
        o(nz0, 'sel 0', 2, 2, ['bang', ''], dx + 240, 500, 40.0)   # id 0 (unmapped): ask nothing
        o(tq, 't b b i', 1, 3, ['bang', 'bang', 'int'], dx + 240, 530, 50.0)
        o(pq, 'prepend id', 1, 1, [''], dx + 300, 560, 70.0)
        m(gmin, 'get min', dx + 240, 560, 50.0)
        m(gmax, 'get max', dx + 240, 590, 50.0)
        o(lmm, 'live.object', 2, 1, [''], dx + 240, 620, 70.0)
        o(rmm, 'route min max', 3, 3, ['', '', ''], dx + 240, 650, 90.0)
        o(tbf, 't b f', 1, 2, ['bang', 'float'], dx + 280, 680, 40.0)
        m(op, '1', dx + 240, 710, 30.0)
        ln((zl, 0, ex, 0), (ex, 0, gt, 1), (gt, 0, pk, 0),
           (tr, 0, tz, 0), (ti, 1, tz, 0),                          # the id: from Map, and after load
           (tz, 1, gt, 0), (tz, 0, nz0, 0), (nz0, 1, tq, 0),
           (tq, 2, pq, 0), (pq, 0, lmm, 1), (tq, 1, gmin, 0), (tq, 0, gmax, 0),   # id, then min, then max
           (gmin, 0, lmm, 0), (gmax, 0, lmm, 0), (lmm, 0, rmm, 0),
           (rmm, 0, ex, 1), (rmm, 1, tbf, 0), (tbf, 1, ex, 2), (tbf, 0, op, 0), (op, 0, gt, 0))
        btn, unb = 'obj-%d' % (3541 + l), 'obj-%d' % (3545 + l)
        ln((pk, 0, lr, 0),
           ('obj-3573', n_l - 1 - l, zl, 0),
           ('obj-3586', l, zl, 1), ('obj-3586', n_l + l, pk, 1),
           (btn, 0, mp, 0), (mp, 0, lm, 0),                       # Map button -> mapping 1/0
           (unb, 0, ub, 0), (ub, 0, um, 0), (ub, 1, s1, 0), (s1, 0, um, 0), (um, 0, lm, 0),
           (s1, 0, 'obj-%d' % (4450 + l), 0), ('obj-%d' % (4450 + l), 0, unb, 0),   # x is a toggle: back off
           (lm, 1, tl, 0), (tl, 1, tr, 0), (tr, 0, nz, 0), (nz, 0, mg, 0),   # 1st: a target is set?
           (tl, 0, lr, 1),                                        # 2nd: id -> live.remote~
           (ri, 1, ms, 0), (ms, 0, mg, 1), (mg, 0, um, 0),        # target refused ("mapping 0"): unmap
           (lm, 3, st, 0), (st, 0, btn, 0),                       # mapping state -> button
           (lm, 2, rs, 0), (rs, 0, sn, 0), (rs, 1, sn, 0),        # name (either form) -> button text
           (sn, 0, nm, 0), (nm, 0, btn, 0),                       # no target: "Map"
           (sn, 1, ts, 0), (ts, 0, sp6, 0), (sp6, 0, pt6, 0), (pt6, 0, btn, 0),   # the target's name
           ('obj-922', 5, gi, 0), (gi, 0, lr, 0),                 # after load: the kept target's name
           (lr, 0, ri, 0), (ri, 0, sz, 0), (sz, 1, ti, 0),
           (ti, 1, pi, 0), (pi, 0, lo, 1), (ti, 0, gn, 0), (gn, 0, lo, 0),
           (lo, 0, rn, 0), (rn, 0, rs, 0))
    # The Map button looks and blinks like Ableton's own (liveui.map): "Map" in LCD text when empty;
    # while it waits for a click on a parameter it fills with the theme's LCD colour, its alpha
    # swapping 1 / 0.5 every 200 ms; mapped, it stays filled and shows the parameter's name.
    o('obj-4380', 'live.thisdevice', 1, 3, ['bang', 'int', 'int'], 900, 700, 90.0)
    m('obj-4381', 'lcd_control_fg', 900, 730, 90.0)
    o('obj-4382', 'live.colors', 1, 2, ['', 'bang'], 900, 760, 70.0)
    o('obj-4383', 'route lcd_control_fg', 2, 2, ['', ''], 900, 790, 120.0)
    ln(('obj-4380', 0, 'obj-4381', 0), ('obj-4381', 0, 'obj-4382', 0), ('obj-4382', 1, 'obj-4381', 0),
       ('obj-4382', 0, 'obj-4383', 0))
    for l in range(n_l):
        dx = 160 * l
        btn, lm = 'obj-%d' % (3541 + l), 'obj-%d' % (3587 + l)
        sn, ts = 'obj-%d' % (3600 + 20 * l + 6), 'obj-%d' % (3600 + 20 * l + 7)
        (bs, st1, bgn, st0, qm, tg, tbi, asl, a1, a5, ps, ap, zr, zs, lc, mf, m0, m1, fs, uc,
         mc) = ('obj-%d' % (4300 + 25 * l + k) for k in range(21))
        o(bs, 'sel 1 0', 1, 3, ['bang', 'bang', ''], dx, 660, 50.0)
        o(st1, 't 1 b', 1, 2, ['int', 'bang'], dx, 690, 40.0)
        m(bgn, 'lcdbgcolor "LCD Background"', dx + 50, 690, 150.0)
        o(st0, 't b 0', 1, 2, ['bang', 'int'], dx + 50, 720, 40.0)
        o(qm, 'qmetro 200', 2, 1, ['bang'], dx, 750, 70.0)
        add_box(P, {'maxclass': 'toggle', 'id': tg, 'numinlets': 1, 'numoutlets': 1, 'outlettype': ['int'],
                    'parameter_enable': 0, 'patching_rect': [X + dx, Y + 780, 20.0, 20.0]})
        o(tbi, 't b i', 1, 2, ['bang', 'int'], dx, 810, 40.0)
        o(asl, 'sel 1 0', 1, 3, ['bang', 'bang', ''], dx + 50, 840, 50.0)
        m(a1, '1.', dx + 50, 870, 30.0)
        m(a5, '0.5', dx + 90, 870, 30.0)
        o(ps, 'prepend set', 1, 1, [''], dx + 50, 900, 70.0)
        o(zr, 'zl reg', 2, 2, ['', ''], dx, 870, 45.0)
        o(zs, 'zl slice 3', 2, 2, ['', ''], dx, 900, 60.0)
        o(ap, 'append 1.', 1, 1, [''], dx, 930, 60.0)
        m(lc, 'lcdcolor $1 $2 $3 $4', dx, 960, 130.0)
        o(m0, 't 0', 1, 1, ['int'], dx, 990, 30.0)
        o(m1, 't 1', 1, 1, ['int'], dx + 40, 990, 30.0)
        o(mf, 'i', 2, 1, ['int'], dx, 1020, 30.0)
        o(fs, 'sel 0 1', 1, 3, ['bang', 'bang', ''], dx, 1050, 50.0)
        m(uc, 'lcdcolor "LCD Text / Icon", lcdbgcolor "LCD Background"', dx, 1080, 150.0)
        m(mc, 'lcdbgcolor "LCD Text / Icon", lcdcolor "LCD Background"', dx, 1110, 150.0)
        # The blink's colour passes a gate that is open only while the button waits. qmetro works through the
        # queue, so one more tick can arrive after the mapping has ended and the final look is set; without
        # the gate that tick painted the text in the fill's colour: a mapped button, solid orange, no name.
        bg = 'obj-%d' % (4300 + 25 * l + 21)
        o(bg, 'gate 1', 2, 1, [''], dx + 140, 960, 45.0)
        ln((st1, 0, bg, 0), (st0, 1, bg, 0), (lc, 0, bg, 1), (bg, 0, btn, 0))
        ln((btn, 0, bs, 0), (lm, 3, bs, 0),                                # mapping starts / stops
           (bs, 0, st1, 0), (st1, 1, bgn, 0), (bgn, 0, btn, 0), (st1, 0, qm, 0),
           (bs, 1, st0, 0), (st0, 1, qm, 0), (st0, 0, mf, 0),
           (qm, 0, tg, 0), (tg, 0, tbi, 0), (tbi, 1, asl, 0), (asl, 0, a1, 0), (asl, 1, a5, 0),
           (a1, 0, ps, 0), (a5, 0, ps, 0), (ps, 0, ap, 0),
           (tbi, 0, zr, 0), ('obj-4383', 0, zr, 1), (zr, 0, zs, 0), (zs, 0, ap, 0), (ap, 0, lc, 0),
           (sn, 0, m0, 0), (ts, 0, m1, 0), (m0, 0, mf, 0), (m1, 0, mf, 0),   # mapped or not: its look
           (mf, 0, fs, 0), (fs, 0, uc, 0), (fs, 1, mc, 0), (uc, 0, btn, 0), (mc, 0, btn, 0))
    # every lane control reports to the stage script (it also reads them all after load)
    # prepends obj-3901..3944
    n = 3900
    def tap(src, text):
        nonlocal n
        n += 1
        o('obj-%d' % n, text, 1, 1, [''], 700 + 130 * ((n - 3901) // 25), 20 * ((n - 3901) % 25), 120.0)
        ln((src, 0, 'obj-%d' % n, 0), ('obj-%d' % n, 0, 'obj-3101', 0))
    for l in range(len(LANES)):
        for i in range(1, 9):
            tap('obj-%d' % (3500 + 8 * l + i), 'prepend modval %d %d' % (l, i))
        tap('obj-%d' % (3551 + l), 'prepend modmin %d' % l)
        tap('obj-%d' % (3555 + l), 'prepend modmax %d' % l)
        tap('obj-%d' % (3559 + l), 'prepend modglide %d' % l)   # Smooth
    assert n == 3900 + 11 * len(LANES)


# ---- Info View texts (every control described, plainly, the 2009 ones rewritten) ----
# One table for the whole face. A drawn part ("... Display", "... Dice", "... Button", "... Icon") shows
# the text of the control it draws. "{s}" is the stage number, "{l}" the lane letter.
INFO = [
    (r'Settings Fold', 'Folds the settings column away, so the device takes less room in the chain. Click again to '
     'open it. The settings keep working while they are folded.'),
    (r'Sequence Length', 'How many stages play, 1 to 8, counting from stage 1. Later stages are skipped: their '
     'columns lose the background and turn grey, and they cannot be changed by hand until Length reaches them. '
     'Global, Reset and the dice still change them; lock a stage to keep it.'),
    (r'Clock Timing', 'One clock step: a 1/32, 1/16, 1/8 or 1/4 note. Each stage lasts its Len in these steps. '
     '1/4 always plays straight.'),
    (r'Direction', 'Order of the stages: > forward, < backward, >< back and forth, R a random stage each time '
     '(it plays its full length).'),
    (r'Swing Amount', 'Delays every second clock step. 0 % is straight, 100 % is half a step late (a dotted '
     'shuffle); a triplet shuffle is about 67 %.'),
    (r'Coarse Tune', 'Moves the whole sequence up or down, -24 to +24 semitones. With a scale on, the notes still '
     'snap to it. Key In adds on top.'),
    (r'Gate', 'Length of every note that does not slide, 1 to 100 % of its full length (one step, or the whole '
     'stage for hold). Lower values play shorter notes. Stages with Slide on keep their full length. 100 % plays '
     'the notes at their full length. A change is heard from the next note.'),
    (r'Shift Left', 'Moves the pattern one stage to the left inside Length: pitch, velocity, Mode, Len, Prob, Slide, '
     'on / off and the padlock move together, and stage 1 wraps around to the last stage in use. Stages beyond '
     'Length and the lane knobs stay. The undo arrow takes it back.'),
    (r'Shift Right', 'Moves the pattern one stage to the right inside Length: pitch, velocity, Mode, Len, Prob, '
     'Slide, on / off and the padlock move together, and the last stage in use wraps around to stage 1. Stages '
     'beyond Length and the lane knobs stay. The undo arrow takes it back.'),
    (r'Lane ([ABC]) Shift Left', 'Moves the knobs of lane {l} one stage to the left inside Length; stage 1 wraps '
     'around to the last stage in use. Min, Max, Smooth and the mapping stay. The undo arrow takes it back.'),
    (r'Lane ([ABC]) Shift Right', 'Moves the knobs of lane {l} one stage to the right inside Length; the last stage '
     'in use wraps around to stage 1. Min, Max, Smooth and the mapping stay. The undo arrow takes it back.'),
    (r'Restart', 'Starts again from stage 1 every 1, 2, 4 or 8 bars, counted from the start of the song, so a '
     'pattern of odd lengths lines up with the bar. With Direction R it picks a new random stage instead. Off: '
     'the sequence runs on freely.'),
    (r'Scale Root', 'Root note of the scale chosen on the right.'),
    (r'Live Root Display', 'Root note of the Live set (Scale is Live). Change it in Live\'s scale settings.'),
    (r'Slice Count Display', 'Slices in the Simpler after this device (Scale is Slices). 0: no Simpler in Slicing '
     'mode follows, so notes play as set.'),
    (r'Scale Select', 'Scale for the notes. Off: notes play as set. Live: the Live set\'s scale and root, while '
     'Live\'s Scale Mode is on. Slices: notes stay inside the slices of the next Simpler in Slicing mode, notes '
     'outside wrap around. Or one of the device\'s own scales, from the root on the left. Notes snap to the nearest '
     'scale note. Scales marked ~ are microtonal: set Bend to your synth\'s pitch bend range.'),
    (r'Bend Range', 'Your synth\'s pitch bend range in semitones. Microtonal scales (~) play the nearest note plus '
     'pitch bend, so set this to match the synth, or the in-between notes come out wrong.'),
    (r'Key In Select', 'Transposes the sequence from notes played into this track (keyboard or clip): C3 leaves it '
     'as is, D3 moves it up a tone. Latch keeps the last note\'s transpose, Hold only while the key is down.'),
    (r'MIDI Thru', 'Lets the MIDI coming into this track through to the instrument, along with the sequence: '
     'CCs, pitch bend, and notes when Key In is off.'),
    (r'Strip Page', 'Sequencer: the stage rows. Mod: the three modulation lanes. The right panel follows.'),
    (r'Stage(\d) LED', 'Stage {s}. Lights while it plays.'),
    (r'Stage(\d) On', 'Click the stage number to switch stage {s} off or on. Off: the stage is silent but keeps its '
     'time in the cycle and its Mode; its column turns grey and takes no clicks until it is on again, except '
     'the padlock (its lane knobs still play). Global, Reset and the dice still change it; lock it to keep it.'),
    (r'PitchDial(\d)', 'Pitch of stage {s} in semitones from C3, -36 to +36. With a scale on, the note snaps to the '
     'scale; the knob keeps its place, so turning the scale off brings the note back.'),
    (r'Random Pitch Row', 'New random pitches for every unlocked stage, from Note range (scale notes only when a '
     'scale is on; with Slices, any slice). The undo arrow takes it back.'),
    (r'VelocityDial(\d)', 'Velocity of stage {s}, 0 to 127. 0 is silent.'),
    (r'Random Velocity Row', 'New random velocities for every unlocked stage, from Velocity range. The undo arrow takes it back.'),
    (r'Stage(\d) Mode Select', 'How stage {s} plays: rest (silent), once (one note at the start), hold (one note for '
     'the whole stage), 1/4, 1/3, 1/2 (a note on every 4th, 3rd or 2nd step) or all (a note on every step).'),
    (r'Stage(\d) Len View', 'Length of stage {s} in clock steps, 1 to 8: click a cell or drag along. Light cells are '
     'the notes; a light block is one held note.'),
    (r'Stage(\d) Probability', 'Chance that stage {s} plays its note, 0 to 100 %. Rolled for every note: once for '
     'once and hold, on each repeat otherwise. When the roll fails the stage stays silent but keeps its time.'),
    (r'Stage(\d) Slide', 'Holds the note of stage {s} into the next note, so a mono synth with glide slides to it, '
     'like a 303. The same pitch next is tied, without a new attack. If no note follows, the note ends 3/4 of a step '
     'after where it would have started.'),
    (r'Stage(\d) Lock', 'Locks stage {s}: Global, Reset, Mutate and the dice leave everything in its column alone, '
     'its lane values included.'),
    (r'Stage(\d) Mode', 'Mode of stage {s}: rest, once, repeat or hold. Set it with the Mode menu on the face.'),
    (r'Stage(\d) Div', 'Repeat interval of stage {s}, every 1 to 4 steps. Set it with the Mode menu (1/4, 1/3, 1/2, '
     'all).'),
    (r'Stage(\d) Length', 'Length of stage {s} in clock steps. Set it with the Len cells.'),
    (r'Mod ([ABC]) Stage(\d)', 'Lane {l} at stage {s}, 0 to 100 %. When the stage starts, the parameter mapped to lane '
     '{l} moves to this point between the lane\'s Min and Max.'),
    (r'Random Lane ([ABC])', 'New random values for lane {l} on every unlocked stage. The undo arrow takes it back.'),
    (r'Mod ([ABC]) Map', 'Click, then click any parameter in Live to give it to lane {l}. Map blinks while it waits; '
     'click it again to cancel. Then it shows the parameter\'s name. A parameter already driven by another lane or '
     'device is refused. The mapping is saved with the set.'),
    (r'Mod ([ABC]) Unmap', 'Frees the parameter of lane {l}.'),
    (r'Mod ([ABC]) Min', 'Where 0 % of lane {l} sits in the mapped parameter\'s range.'),
    (r'Mod ([ABC]) Max', 'Where 100 % of lane {l} sits in the mapped parameter\'s range. Set Max below Min to turn '
     'the lane upside down.'),
    (r'Mod ([ABC]) Smooth', 'Glide between the values of lane {l}, in ms. 0 jumps at each stage start.'),
    (r'Random Lanes', 'New random values for all three lanes on every unlocked stage. Min, Max, Smooth and the '
     'mappings stay. The undo arrow takes it back.'),
    (r'Reset Lanes', 'Puts the Mod page back: all three lanes\' knobs to 0 % on every unlocked stage, Min 0 %, Max '
     '100 %, Smooth 0 ms. The mappings stay. The '
     'undo arrow takes it back.'),
    (r'Random All', 'New random values on every unlocked stage for the parts ticked below: pitch from Note range, '
     'velocity from Velocity range, Prob 25 to 100 %, Slide on about one stage in four, and Mode, Div, Len, Seq '
     'Length. The undo arrow takes it back.'),
    (r'Undo', 'Takes back the last Global, Reset, Mutate (Auto\'s too), die or shift arrow in one click, and only what that tool '
     'changed: what you set by hand afterwards stays. Each click goes one more back, up to 32 (Cmd+Z in Live goes '
     'back in smaller steps). Grey when there is nothing left to undo.'),
    (r'Reset Ticked', 'Puts the Sequencer page back: every unlocked stage to pitch 0 st, velocity 120, Mode once, '
     'Len 1, Prob 100 %, Slide off, stage on, whatever is ticked below. Length and the settings on the left stay. '
     'The undo arrow takes it back.'),
    (r'Random All Pitch', 'Tick to let Global and Mutate change the pitches.'),
    (r'Random All Velocity', 'Tick to let Global and Mutate change the velocities.'),
    (r'Random All St\. Mode', 'Tick to let Global and Mutate change the Modes (rest, once, hold, repeats).'),
    (r'Random All St\. Div', 'Tick to let Global and Mutate change how often a repeating stage repeats.'),
    (r'Random All St\. Length', 'Tick to let Global and Mutate change the stage lengths (Len).'),
    (r'Random All Seq Length', 'Tick to let Global change how many stages play. Mutate and Reset leave it alone.'),
    (r'Random All Probability', 'Tick to let Global and Mutate change Prob (Global picks 25, 50, 75 or 100 %).'),
    (r'Random All Slide', 'Tick to let Global and Mutate change Slide (Global turns it on for about one stage in '
     'four).'),
    (r'Note Range Low', 'Lowest note Global, Mutate and the Pitch die pick. With a scale on, only scale notes.'),
    (r'Note Range High', 'Highest note Global, Mutate and the Pitch die pick. With a scale on, only scale notes.'),
    (r'Velocity Range Low', 'Lowest velocity Global, Mutate and the Vel die pick.'),
    (r'Velocity Range High', 'Highest velocity Global, Mutate and the Vel die pick.'),
    (r'Mutate', 'New values for one random stage, for the parts ticked above, picked among the stages that play '
     '(inside Length and switched on) and are not locked. Seq Length stays. Same ranges and scale as Global. The undo arrow takes it back.'),
    (r'Mutate Auto', 'On: Mutate runs by itself every 4, 8 or 16 bars (set on the right), at the end of the bar, so '
     'the next bar starts changed. Bars count from the song position: playing from bar 1, the changes come before '
     'bars 5, 9, 13 and so on. Each change is one step for the undo arrow.'),
    (r'Mutate Every', 'How often Auto mutates: every 4, 8 or 16 bars. Grey while Auto is off; switch Auto on to '
     'change it.'),
    (r'Lane Mutate', 'New values on all three lanes for one random stage, picked among the stages that play and are '
     'not locked. Min, Max, Smooth and the mappings stay. The undo arrow takes it back.'),
    (r'Lane Mutate Auto', 'On: the lanes\' Mutate runs by itself every 4, 8 or 16 bars (set on the right), at the end '
     'of the bar, apart from the Sequencer page\'s Auto. Each change is one step for the undo arrow.'),
    (r'Lane Mutate Every', 'How often the lanes\' Auto mutates: every 4, 8 or 16 bars. Grey while Auto is off; '
     'switch Auto on to change it.'),
    (r'Scale Source', 'Where the scale comes from: Off, Live, Own or Slices. Set it with the Scale menu.'),
    (r'Scale', 'The device\'s own scale. Set it with the Scale menu.'),
    (r'Key In', 'Key In on or off. Set it with the Key In switch.'),
    (r'Key Mode', 'Key In Latch or Hold. Set it with the Key In switch.'),
]
DRAWN_SUFFIX = (' Display', ' Dice', ' Button', ' Icon')
ALIAS = {'Global': 'Random All', 'Reset': 'Reset Ticked', 'Swing': 'Swing Amount'}   # drawn part -> its control


def info_for(name):
    for pat, text in INFO:
        m = re.fullmatch(pat, name)
        if m:
            g = [x for x in m.groups()]
            lane = next((x for x in g if x in 'ABC'), '')
            stage = next((x for x in g if x.isdigit()), '')
            return text.replace('{s}', stage).replace('{l}', lane)
    return None


def info_texts(P):
    """Every control on the face (and every parameter) gets its Info View text from INFO."""
    for b in P['boxes']:
        x = b['box']
        name = x.get('varname')
        if not name:
            continue
        base = name
        for suf in DRAWN_SUFFIX:
            stem = base[:-len(suf)] if base.endswith(suf) else None
            if stem is not None and (info_for(stem) or stem in ALIAS):
                base = ALIAS.get(stem, stem)
                break
        text = info_for(base)
        if text:
            x['annotation'] = text


# Push banks: saved with the device; 8 parameters each, by long
# name. Main first: what you play with live. Then Mutate, one bank per stage row, the scale, the lanes.
def push_banks():
    st = lambda fmt: [fmt % i for i in range(1, 9)]
    banks = [('Main', ['Sequence Length', 'Clock Timing', 'Direction', 'Swing Amount', 'Coarse Tune', 'Restart',
                       'Key In', 'Scale Source']),
             ('Mutate', ['Mutate Auto', 'Mutate Every', 'Lane Mutate Auto', 'Lane Mutate Every', '-', '-', '-', '-']),
             ('Pitch', st('PitchDial%d')), ('Velocity', st('VelocityDial%d')), ('Mode', st('Stage%d Mode')),
             ('Len', st('Stage%d Length')), ('Prob', st('Stage%d Probability')), ('Slide', st('Stage%d Slide')),
             ('On', st('Stage%d On')), ('Div', st('Stage%d Div')), ('Scale', ['Scale Source', 'Scale Root', 'Scale', 'Key In', 'MIDI Thru',
                                                    '-', '-', '-']),
             ('Lane A', st('Mod A Stage%d')), ('Lane B', st('Mod B Stage%d')), ('Lane C', st('Mod C Stage%d'))]
    return {str(k): {'index': k, 'name': name, 'parameters': params} for k, (name, params) in enumerate(banks)}


def theme_feed(P):
    """Every drawing (jsui running ml185box.js) gets Live's theme colours: on load (after live.thisdevice)
    and whenever the theme changes, live.colors reports everything; the colours the drawings use go out as
    "themecolor <name> r g b a" to all of them. Also: each drawing's patching size equals its face size
    (jsui may size its drawing from either). Ids obj-4600..4613."""
    R = boxes(P)
    draws = [b['box'] for b in P['boxes'] if b['box']['maxclass'] == DRAW_CLASS and b['box'].get('filename') == 'ml185box.js']
    assert draws, 'no drawings'
    X, Y = 3600.0, 1200.0
    add_box(P, message('obj-4601', 'everything', [X, Y + 30, 70.0, 20.0]))
    add_box(P, newobj('obj-4602', 'live.colors', 1, 2, ['', 'bang'], [X, Y + 60, 70.0, 20.0]))
    add_box(P, newobj('obj-4603', 'route ' + ' '.join(THEME_NAMES), 1, len(THEME_NAMES) + 1,
                      [''] * (len(THEME_NAMES) + 1), [X, Y + 90, 520.0, 20.0]))
    add_box(P, newobj('obj-4611', 's ---themecolors', 1, 0, [], [X, Y + 150, 100.0, 20.0]))
    add_box(P, newobj('obj-4600', 'r ---themecolors', 0, 1, [''], [X + 200, Y + 150, 100.0, 20.0]))
    assert R['obj-921']['text'] == 'deferlow'             # after live.thisdevice, as the rest of the load
    add_line(P, 'obj-921', 0, 'obj-4601', 0)
    add_line(P, 'obj-4602', 1, 'obj-4601', 0)            # the theme changed
    add_line(P, 'obj-4601', 0, 'obj-4602', 0)
    add_line(P, 'obj-4602', 0, 'obj-4603', 0)
    for k, name in enumerate(THEME_NAMES):
        pid = 'obj-%d' % (4604 + k if k < 7 else 4605 + k)     # 4611 is the send
        add_box(P, newobj(pid, 'prepend themecolor ' + name, 1, 1, [''], [X + 75 * k, Y + 120, 70.0, 20.0]))
        add_line(P, 'obj-4603', k, pid, 0)
        add_line(P, pid, 0, 'obj-4611', 0)
    for x in draws:
        if x.get('presentation_rect'):
            x['patching_rect'] = list(x['patching_rect'][:2]) + list(x['presentation_rect'][2:4])
        add_line(P, 'obj-4600', 0, x['id'], 0)


def fix_pass(P):
    """Names Push and automation show, leftovers off the automation list,
    the Sequencer / Mod tab out of the undo history, pitch bend back to centre on stop.
    Ids obj-4500..4501."""
    R = boxes(P)
    byname = {}
    for b in P['boxes']:
        v = b['box'].get('saved_attribute_attributes', {}).get('valueof', {})
        if 'parameter_longname' in v:
            byname[v['parameter_longname']] = b['box']
    def short(long, sname, enum=None):
        v = byname[long]['saved_attribute_attributes']['valueof']
        v['parameter_shortname'] = sname
        if enum:
            v['parameter_enum'] = enum
    short('Swing Amount', 'Swing')
    short('Coarse Tune', 'Transpose')
    short('Clock Timing', 'Rate')
    short('Direction', 'Direction')
    short('MIDI Thru', 'Thru', ['off', 'on'])
    for i in range(1, 9):                       # enum labels only: the tabs' index output is what is wired
        short('Stage%d Mode' % i, 'St%d Mode' % i, ['rest', 'once', 'repeat', 'hold'])
        short('Stage%d Div' % i, 'St%d Div' % i)
        short('Stage%d Length' % i, 'St%d Len' % i)
    # the 2009 layer switch (Pitch / Vel / Aux, its Aux gone) is internal only now
    byname['Pitch/Vel/Aux Layer']['saved_attribute_attributes']['valueof']['parameter_invisible'] = 2
    # Global Step Division: off the face, and at load it rewrote every stage's Div
    assert R['obj-115']['varname'] == 'Global Step Division'
    remove_box(P, 'obj-115')
    # the Sequencer / Mod tab is a view, not a setting: not stored, so it stays out of Live's undo
    byname['Strip Page']['saved_attribute_attributes']['valueof']['parameter_invisible'] = 2
    # Not done: ending the held note on a transport jump (---xreset). The jump detector also fired
    # without a real jump when the scheduler ran late (a 10-tick poll seeing > 40 ticks), and then it
    # cut held notes. A held note ends after its own length instead.
    # A late poll counts as a jump only when its gap is longer than one clock step (edit, (a)),
    # but a long scheduler stall still can, so held notes keep ending by their own length.
    # pitch bend back to centre on stop and device off while a microtonal scale is on
    add_box(P, newobj('obj-4500', 'r ---xstop', 0, 1, [''], [1150.0, 460.0, 70.0, 20.0]))
    add_box(P, message('obj-4501', 'stopped', [1150.0, 490.0, 55.0, 20.0]))
    add_line(P, 'obj-4500', 0, 'obj-4501', 0)
    add_line(P, 'obj-4501', 0, 'obj-930', 0)


MAPPABLE = (('obj-980', 'obj-998', 'obj-987', 'Random All'), ('obj-3401', 'obj-3403', 'obj-3404', 'Mutate'),
            ('obj-4700', 'obj-4701', 'obj-4702', 'Lane Mutate'), ('obj-4463', 'obj-4464', 'obj-4465', 'Random Lanes'))


def mappable_buttons(P):
    """Global, Mutate, Lane Mutate and the lanes' Random become visible parameters, so Live's key and
    MIDI map modes show them. Live's key and note mappings toggle a two-state parameter (1, 0, 1, ...), so
    each of these is a toggle and every change of it runs the action: a mapped key works on every press,
    and so does a click on the face. (A first version kept the button mode and sent the button a 0 after
    each bang; in Live the mapping's own toggle did not follow, so every second press did nothing.)
    A stored toggle reports its value while a set loads: that must not run the action, so a gate in front
    opens only after the load bang. Known: a momentary CC (127, then 0 on release) changes the toggle twice
    and runs the action twice.
    Live's undo or redo of such a change flips the toggle back, which must not run the action again:
    a change goes gate, t b, "mapped <drawn button>" to the script, which runs the action only for a
    real press (see mapped in ml185scale.js) by having the drawn button press itself (bang to the
    action, flash). The drawn button is also set to take a click directly; in Live the parameter
    gets the click all the same, which is what lets the map modes pick it.
    Ids obj-4434..4441 (gate, t b per button), 4442..4443 (the gates open after load), 4444..4447 (mapped)."""
    R = boxes(P)
    assert R['obj-922']['text'] == 't b b b b b b'
    add_box(P, newobj('obj-4442', 'delay 200', 2, 1, ['bang'], [1400.0, 1500.0, 60.0, 20.0]))
    add_box(P, message('obj-4443', '1', [1400.0, 1530.0, 30.0, 20.0]))
    add_line(P, 'obj-922', 0, 'obj-4442', 0)
    add_line(P, 'obj-4442', 0, 'obj-4443', 0)
    for k, (btn, rb, s1, long) in enumerate(MAPPABLE):
        v = R[btn]['saved_attribute_attributes']['valueof']
        assert v['parameter_longname'] == long and R[rb]['text'] == 'route bang' and R[s1]['text'] == 'sel 1'
        assert R[btn].get('mode', 1) == 0 and not v.get('parameter_initial_enable')
        v['parameter_invisible'] = 0
        v['parameter_enum'] = ['off', 'on']
        R[btn]['mode'] = 1
        target = [l['patchline']['destination'][0] for l in P['lines'] if l['patchline']['source'] == [rb, 0]]
        draw = [l['patchline']['destination'][0] for l in P['lines']
                if l['patchline']['source'] == [btn, 0] and R[l['patchline']['destination'][0]]['maxclass'] == DRAW_CLASS]
        assert len(target) == 1 and R[target[0]]['maxclass'] == 'message' and len(draw) == 1, long
        remove_line(P, rb, 1, s1, 0)                 # the 1 / 0 no longer goes through sel 1 ...
        remove_line(P, s1, 0, target[0], 0)
        remove_box(P, s1)
        remove_line(P, btn, 0, draw[0], 0)           # ... and the drawing flashes only for a real press
        gt, tb = 'obj-%d' % (4434 + 2 * k), 'obj-%d' % (4435 + 2 * k)
        x, y = R[btn]['patching_rect'][:2]
        add_box(P, newobj(gt, 'gate 1', 2, 1, [''], [x + 120, y + 30, 45.0, 20.0]))      # closed while loading
        add_box(P, newobj(tb, 't b', 1, 1, ['bang'], [x + 120, y + 60, 30.0, 20.0]))
        mp = 'obj-%d' % (4444 + k)
        add_box(P, message(mp, 'mapped ' + R[draw[0]]['varname'], [x + 120, y + 90, 140.0, 20.0]))
        R[draw[0]].update(ignoreclick=0, numoutlets=1, outlettype=[''])      # the click: drawn button -> action
        for a_, ao, b_, bi in ((rb, 1, gt, 1), ('obj-4443', 0, gt, 0), (gt, 0, tb, 0), (tb, 0, mp, 0),
                               (mp, 0, 'obj-930', 0), (draw[0], 0, target[0], 0)):
            add_line(P, a_, ao, b_, bi)


def remove_aux_and_length_mode(P):
    """Aux (per-stage CC / pitch bend) leaves the device, and so does the Length Mode
    button (Stages / Fixed) with the ticks readout: the sequence always runs in Stages mode (the
    gswitch keeps its default, Stages). Their parameters are gone; old sets load without them."""
    R = boxes(P)
    assert R['obj-2']['text'] == 'gate 2' and R['obj-236']['text'] == 'bendout' and R['obj-85']['text'] == 'ctlout'
    assert R['obj-103']['text'] == 'r ---auxCC' and R['obj-157']['varname'] == 'Length Mode'
    for bid in ('obj-2', 'obj-236', 'obj-85', 'obj-103', 'obj-207', 'obj-950', 'obj-951', 'obj-157'):
        remove_box(P, bid)
    for b in list(P['boxes']):                      # the script tap of the removed button
        if b['box'].get('text') == 'prepend lengthmode':
            remove_box(P, b['box']['id'])


MODE_NAMES = ['rest', 'once', 'hold', '1/4', '1/3', '1/2', 'all']
MODE_TEXT = ('How this stage plays: rest (silent), once (one note at the start), hold (one note for the whole '
             'stage), 1/4, 1/3, 1/2 (a note on every 4th, 3rd or 2nd step) or all (a note on every step). '
             'Click to choose.')
LEN_TEXT = ('Length of this stage in clock steps: click a cell, or drag along, to set it. The cells show where '
            'the notes play: white cells are notes, a white block is one held note.')


def p6_controls(P, menu_t, stage_len):
    """STEPS page: one Mode word instead of the Mode and Div tabs, and the Len cells
    with the notes drawn in (tied cells) instead of the 1-8 tab. Display and input only: the Stage
    Mode / Div / Length tabs stay the parameters (hidden), so sets and automation are unchanged.
    The Mode word is a dropdown (hidden parameter, not saved); choosing asks ml185scale.js to write
    Mode and Div. The Len cells are a clickable drawing (jsui) that sends the tab index to the Length tab.
    ml185stage.js shows both from the stage values. Ids obj-4001..4038."""
    mode_ids, len_ids = [], []
    for i in range(1, 9):
        bid = 'obj-%d' % (4000 + i)
        b = clone(menu_t, bid, [3000.0, 900.0 + 20 * i, 43.0, 15.0], None, varname='Stage%d Mode Select' % i,
                  annotation=MODE_TEXT)
        b.pop('hidden', None)
        param(b, parameter_enum=MODE_NAMES, parameter_initial=[1], parameter_initial_enable=0,
              parameter_mmin=0.0, parameter_mmax=6.0, parameter_longname='Stage%d Mode Select' % i,
              parameter_shortname='St%d Mode' % i, parameter_invisible=2)
        add_box(P, b)
        pre = 'obj-%d' % (4030 + i)
        add_box(P, newobj(pre, 'prepend modepick %d' % i, 1, 1, [''], [3060.0, 900.0 + 20 * i, 110.0, 20.0]))
        add_line(P, bid, 0, pre, 0)
        add_line(P, pre, 0, 'obj-930', 0)
        mode_ids.append(bid)
        lid = 'obj-%d' % (4020 + i)
        add_box(P, {'maxclass': DRAW_CLASS, 'id': lid, 'varname': 'Stage%d Len View' % i, 'filename': 'ml185box.js',
                    'jsarguments': ['len', 0, 7], 'numinlets': 1, 'numoutlets': 1, 'outlettype': [''],
                    'parameter_enable': 0, 'border': 0, 'ignoreclick': 0,
                    'patching_rect': [3180.0, 900.0 + 20 * i, 47.0, 13.0],
                    'presentation': 1, 'presentation_rect': [0, 0, 47, 13], 'annotation': LEN_TEXT})
        add_line(P, lid, 0, stage_len[i - 1], 0)
        len_ids.append(lid)
    return mode_ids, len_ids


# Face names for the Scale menu where the full name does not fit 86 px (measured in Live's font);
# the stored Scale parameter keeps the full names.
SCALE_SHORT = {'Phrygian Dominant': 'Phrygian Dom.', 'Hungarian Minor': 'Hungarian Min.',
               'Major Pentatonic': 'Major Pent.', 'Minor Pentatonic': 'Minor Pent.',
               'Harmonic 8-16 ~': 'Harm. 8-16 ~'}


def scale_select(P, menu_t):
    """Left column: Source merged into the Scale menu. One hidden-parameter menu
    Off / Live / the device's own scales that writes the stored Scale Source and Scale parameters
    (ml185scale.js scalepick) and shows them back (updateScaleDisplay). Ids obj-4420..4421."""
    R = boxes(P)
    names = R['obj-903']['saved_attribute_attributes']['valueof']['parameter_enum']
    enum = ['Off', 'Live', 'Slices'] + [SCALE_SHORT.get(n, n) for n in names]
    b = clone(menu_t, 'obj-4420', [3300.0, 1000.0, 86.0, 15.0], None, varname='Scale Select', fontsize=9.0,
              annotation='Scale: Off (notes play as set); Live (follow the Live set\'s scale and root while '
                         'Live\'s Scale Mode is on; with a Tuning System loaded, notes pass through and Live '
                         'retunes them); or one of the device\'s own scales, from the Root on the left. Notes '
                         'snap to the nearest scale note. Scales marked ~ are microtonal: nearest semitone plus '
                         'pitch bend, so set Bend to match your synth.')
    b.pop('hidden', None)
    param(b, parameter_enum=enum, parameter_initial=[0], parameter_initial_enable=0,
          parameter_longname='Scale Select', parameter_shortname='Scale Sel', parameter_mmin=0.0,
          parameter_mmax=float(len(enum) - 1), parameter_invisible=2)
    add_box(P, b)
    add_box(P, newobj('obj-4421', 'prepend scalepick', 1, 1, [''], [3300.0, 1030.0, 100.0, 20.0]))
    add_line(P, 'obj-4420', 0, 'obj-4421', 0)
    add_line(P, 'obj-4421', 0, 'obj-930', 0)
    return 'obj-4420'


def names_by_group(groups):
    out = {}
    for g, names in groups.items():
        for nm in names:
            out.setdefault(nm, []).append(g)
    return out


def swing_quarter(text):
    """swingCalc.js: Rate 1/4 (clock index 3) runs straight; swing keeps acting on 1/32, 1/16, 1/8."""
    old = '    }\n}\nrecalcVals.local = 1;'
    assert old in text and 'case 3:' not in text
    return text.replace(old, '''        case 3:    // Quarter notes: the subdivision swing table stays straight.
            currVals[0] = 0;
            for (var k = 1; k < 8; k++) currVals[k] = 480;
            outputVals();
            break;
''' + old, 1)


def main(orig_path, js_path, out_path):
    box_path = os.path.join(os.path.dirname(os.path.abspath(js_path)), 'ml185box.js')   # the drawings' script, next to it
    stage_path = os.path.join(os.path.dirname(os.path.abspath(js_path)), 'ml185stage.js')   # stage tables
    raw = open(orig_path, 'rb').read()
    chunks = read_chunks(raw)
    assert [c[0] for c in chunks] == [b'ampf', b'meta', b'ptch']
    files = parse_mxc(chunks[2][1])
    assert write_chunks([chunks[0], chunks[1], [b'ptch', build_mxc(files)]]) == raw, 'container round trip'
    assert files[0]['type'] == b'JSON' and files[1]['name'] == 'swingCalc.js' and len(files) == 2

    js_src = open(js_path, 'rb').read()
    doc = json.loads(files[0]['data'].rstrip(b'\0').decode('utf-8'))
    doc = edit(doc, js_src.decode('utf-8'))
    files[0]['data'] = json.dumps(doc, indent='\t').encode('utf-8') + b'\n\0'
    files[1]['data'] = swing_quarter(files[1]['data'].decode('utf-8')).encode('utf-8')
    files[0]['mdat'] = hfs_now()
    files.append({'type': b'TEXT', 'name': 'ml185scale.js', 'data': js_src,
                  'vers': b'\0\0\0\0', 'flag': b'\0\0\0\0', 'mdat': hfs_now()})
    files.append({'type': b'TEXT', 'name': 'ml185box.js', 'data': open(box_path, 'rb').read(),
                  'vers': b'\0\0\0\0', 'flag': b'\0\0\0\0', 'mdat': hfs_now()})
    files.append({'type': b'TEXT', 'name': 'ml185stage.js', 'data': open(stage_path, 'rb').read(),
                  'vers': b'\0\0\0\0', 'flag': b'\0\0\0\0', 'mdat': hfs_now()})
    chunks[2][1] = build_mxc(files)
    open(out_path, 'wb').write(write_chunks(chunks))


if __name__ == '__main__':
    main(*sys.argv[1:4])
