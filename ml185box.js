// ml185box.js (runs in jsui: classic JavaScript only, no let/const/arrows)
// v8ui value display for the ML-185 number boxes (Swing, Bend Range, Note and Velocity Range).
//
// Draws the value like the stage tabs: Live's "Control On" orange fill, text in the normal
// text colour over the background and in the "on" text colour (black) where the fill
// covers it, split through a letter if needed (a hard-edged gradient on the text). Display only: this box ignores clicks, so
// the live.numbox underneath still handles dragging, typing, mapping and saving, and
// sends its value here. The number box itself is made fully transparent by the build.
//
// Arguments: kind lo hi    kind = note | int | st | pct | ipct (whole percent) | ms, dice (a die icon
// for a Random button, drawn under the transparent
// live.text that takes the click and flashes while pressed), or len (a stage's length as 8 cells with
// its notes drawn in: this one takes clicks and sends the new length out, tab index 0..7), or button
// (kind lo hi size label [die]: a push button drawn like Live's own, optionally with a die before the
// label, over a fully transparent live.text that takes the click and sends it here to flash), or fold
// (the settings-column toggle: a gear and an arrow, < while open, > while folded, lit while folded),
// or lock (a stage's random lock: a padlock in a toggle pill, closed and lit while locked).

mgraphics.init();
mgraphics.relative_coords = 0;
mgraphics.autofill = 0;

inlets = 1;
outlets = 1;

var self = this;
var kind = String(jsarguments.length > 1 ? jsarguments[1] : "int");
var lo = Number(jsarguments.length > 2 ? jsarguments[2] : 0);
var hi = Number(jsarguments.length > 3 ? jsarguments[3] : 127);
var value = lo;

var NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
// len: the stage's Mode tab index (0 rest, 1 one note, 2 repeats, 3 hold), Div (1..4), Length (1..8)
var lv = { mode: 1, div: 1, len: 1 };
var FONT = "Ableton Sans Medium";   // the font Ableton's own Live-theme scripts use in Max
var FONT_SIZE = jsarguments.length > 4 ? Number(jsarguments[4]) : 9;
var PLAIN = jsarguments.length > 5 && String(jsarguments[5]) === "plain";   // the number only, no bar
// button: one control for die + label (not a die and a button doing the same thing)
var BTN_LABEL = kind === "button" && jsarguments.length > 5 ? String(jsarguments[5]) : "";
var BTN_ICON = kind === "button" && jsarguments.length > 6 ? String(jsarguments[6]) : "";   // "die" or "undo"
var BTN_DIE = BTN_ICON === "die";
var pressed = 0;
var pressTask = null;
var btnEnabled = 1;   // "enabled 0": nothing to do (Back with nothing to restore), drawn dim, no flash

// Used only if Live's theme colours cannot be read.
// Theme colours are the ones Live's own number box uses (Max reference defaults):
// background live_control_text_bg, text live_control_fg, border live_contrast_frame;
// the fill and the text over it are the tabs' "on" pair.
var FALLBACK = {
	live_control_bg: [0.26, 0.26, 0.26, 1],
	live_control_fg_zombie: [0.58, 0.58, 0.58, 1],
	live_control_text_bg: [0.2, 0.2, 0.2, 1],
	live_contrast_frame: [0.5, 0.5, 0.5, 1],
	live_control_fg: [0.85, 0.85, 0.85, 1],
	live_control_selection: [1, 0.709804, 0.196078, 1],
	live_control_fg_on: [0, 0, 0, 1]
};

function msg_int(v) { if (kind === "button") { if (Number(v)) flash(); return; } setValue(v); }
function enabled(v) { btnEnabled = Number(v) ? 1 : 0; mgraphics.redraw(); }
function msg_float(v) { msg_int(v); }
function bang() { if (kind === "button") flash(); }

// A click on the button: Live's pressed look (orange, dark text) for a moment.
function flash() {
	if (!btnEnabled) return;
	pressed = 1;
	mgraphics.redraw();
	try {
		if (!pressTask) pressTask = new Task(release, this);
		pressTask.cancel();
		pressTask.schedule(150);
	} catch (e) {
		release();
	}
}
function release() {
	pressed = 0;
	mgraphics.redraw();
}

function setValue(v) {
	v = Number(v);
	if (!isFinite(v)) return;
	value = v;
	mgraphics.redraw();
}

function label(v) {
	if (kind === "note") {
		var n = Math.round(v);
		return NOTE_NAMES[((n % 12) + 12) % 12] + (Math.floor(n / 12) - 2);
	}
	if (kind === "st") {
		var s = Math.round(v);
		return (s > 0 ? "+" : "") + s + " st";
	}
	if (kind === "ipct") return Math.round(v) + " %";
	if (kind === "ms") return Math.round(v) + " ms";
	if (kind === "pct") {
		var a = Math.abs(v);
		return v.toFixed(a < 10 ? 2 : (a < 100 ? 1 : 0)) + " %";
	}
	return String(Math.round(v));
}

function fraction(v) {
	if (PLAIN || !(hi > lo)) return 0;
	return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
}

// Live's theme colours come in from a live.colors object in the patch, as in Ableton's own jsui
// devices: "themecolor control_fg r g b a" (live.colors names have no "live_" prefix). Until they
// arrive, or where Max can tell itself (max.getcolor, in Max 9), the fallbacks below are used.
var COLORS = {};
function themecolor(name, r, g, b, a) {
	COLORS["live_" + String(name)] = [Number(r), Number(g), Number(b), a === undefined ? 1 : Number(a)];
	mgraphics.redraw();
}

function color(id) {
	if (COLORS[id]) return COLORS[id];
	try {
		var c = max.getcolor(id);
		if (c && c.length >= 3) return c;
	} catch (e) {}
	return FALLBACK[id];
}

// Drawing size: mgraphics.size, as in Max's own v8ui template. box.rect can come back in screen
// pixels in Live (about twice the size on a Retina display), which made the drawing too big.
function area() {
	var w = 0, h = 0;
	try {
		var s = mgraphics.size;
		w = Number(s[0]);
		h = Number(s[1]);
	} catch (e) {}
	if (!(w > 0 && h > 0)) {
		var r = self.box.rect;
		w = r[2] - r[0];
		h = r[3] - r[1];
	}
	return [w, h];
}

// mgraphics has no clipping, so the two-colour text is one show_text with a hard-edged
// linear gradient: "on" text colour up to the fill edge, normal text colour after it.
// Only calls Max's own scripts use: rectangle/fill/stroke, set_line_width, text_measure,
// show_text, pattern_create_linear + add_color_stop_rgba + set_source.
function paint() {
	var g = mgraphics;
	var a = area();
	var w = a[0];
	var h = a[1];
	if (kind === "dice") {
		paintDice(g, w, h);
		return;
	}
	if (kind === "button") {
		paintButton(g, w, h);
		return;
	}
	if (kind === "fold") {
		paintFold(g, w, h);
		return;
	}
	if (kind === "lock") {
		paintLock(g, w, h);
		return;
	}
	if (kind === "len") {
		paintLen(g, w, h);
		return;
	}
	var fw = Math.round(w * fraction(value));
	var text = label(value);
	var on = color("live_control_fg_on");
	var off = color("live_control_fg");

	g.set_source_rgba(color("live_control_text_bg"));
	g.rectangle(0, 0, w, h);
	g.fill();
	if (fw > 0) {
		g.set_source_rgba(color("live_control_selection"));
		g.rectangle(0, 0, fw, h);
		g.fill();
	}

	g.select_font_face(FONT);
	g.set_font_size(FONT_SIZE);
	var tw = g.text_measure(text)[0];
	var tx = Math.round((w - tw) / 2);
	var ty = Math.round(h / 2 + FONT_SIZE * 0.35);   // baseline: text centred in the box

	if (fw <= 0) {
		g.set_source_rgba(off);
	} else if (fw >= w) {
		g.set_source_rgba(on);
	} else {
		var t = fw / w;
		var pat = g.pattern_create_linear(0, 0, w, 0);
		pat.add_color_stop_rgba(0, on[0], on[1], on[2], on[3]);
		pat.add_color_stop_rgba(t, on[0], on[1], on[2], on[3]);
		pat.add_color_stop_rgba(t, off[0], off[1], off[2], off[3]);
		pat.add_color_stop_rgba(1, off[0], off[1], off[2], off[3]);
		g.set_source(pat);
	}
	g.move_to(tx, ty);
	g.show_text(text);

	g.set_source_rgba(color("live_contrast_frame"));   // 1 px border, as on Live's number boxes
	g.set_line_width(1);
	g.rectangle(0.5, 0.5, w - 1, h - 1);
	g.stroke();
}

// A die showing five: rounded square outline and five dots, in the theme's text colour.
function paintDice(g, w, h) {
	var sz = Math.min(w, h) - 2;
	drawDie(g, (w - sz) / 2, (h - sz) / 2, sz, color("live_control_fg"));
}

function drawDie(g, x, y, sz, col) {
	g.set_source_rgba(col);
	g.set_line_width(1);
	try {
		g.rectangle_rounded(x + 0.5, y + 0.5, sz - 1, sz - 1, 3, 3);
	} catch (e) {
		g.rectangle(x + 0.5, y + 0.5, sz - 1, sz - 1);
	}
	g.stroke();
	var r = Math.max(0.8, sz * 0.09);
	var pips = [[0.28, 0.28], [0.72, 0.28], [0.5, 0.5], [0.28, 0.72], [0.72, 0.72]];
	for (var i = 0; i < pips.length; i++) {
		g.ellipse(x + sz * pips[i][0] - r, y + sz * pips[i][1] - r, 2 * r, 2 * r);
		g.fill();
	}
}

// ---- button: Live's push button look, measured in Max (2026-11-12): fill live_control_bg, 1 px
// border live_contrast_frame, text live_control_fg; pressed: live_control_selection with
// live_control_fg_on text. Round ends. The die and the label are centred together.
function paintButton(g, w, h) {
	var fill = color(pressed ? "live_control_selection" : "live_control_bg");
	var fg = color(pressed ? "live_control_fg_on" : (btnEnabled ? "live_control_fg" : "live_control_fg_zombie"));
	g.set_source_rgba(fill);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.fill();
	g.set_source_rgba(color("live_contrast_frame"));
	g.set_line_width(1);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.stroke();
	if (BTN_ICON === "undo") {                          // an undo arrow and no word
		drawUndo(g, w / 2, h / 2, fg);
		return;
	}
	g.select_font_face(FONT);
	g.set_font_size(FONT_SIZE);
	var tw = g.text_measure(BTN_LABEL)[0];
	var ds = BTN_DIE ? Math.max(7, Math.min(11, h - 4)) : 0;
	var gap = BTN_DIE ? 4 : 0;
	var x0 = Math.round((w - (ds + gap + tw)) / 2);
	if (BTN_DIE) drawDie(g, x0, Math.round((h - ds) / 2), ds, fg);
	g.set_source_rgba(fg);
	g.move_to(x0 + ds + gap, Math.round(h / 2 + FONT_SIZE * 0.35));
	g.show_text(BTN_LABEL);
}

// ---- fold: the settings column's toggle (a bigger arrow and a settings sign).
// Same look as the buttons; lit (Live's "on" pair) while the column is folded.
function paintFold(g, w, h) {
	var on = Number(value) ? 1 : 0;
	var fill = color(on ? "live_control_selection" : "live_control_bg");
	var fg = color(on ? "live_control_fg_on" : "live_control_fg");
	g.set_source_rgba(fill);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.fill();
	g.set_source_rgba(color("live_contrast_frame"));
	g.set_line_width(1);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.stroke();
	var R = Math.max(3, Math.min(5.5, h / 2 - 1.5));   // gear radius
	var gap = 5;
	var cw = 4;                                          // arrow width
	var x0 = Math.round((w - (2 * R + gap + cw)) / 2);
	drawGear(g, x0 + R, h / 2, R, fg, fill);
	var ax = x0 + 2 * R + gap;
	var ay = h / 2;
	var ah = Math.min(3.5, h / 2 - 2.5);
	g.set_source_rgba(fg);
	g.set_line_width(1.5);
	if (on) {                                            // folded: > opens
		g.move_to(ax, ay - ah); g.line_to(ax + cw, ay); g.line_to(ax, ay + ah);
	} else {                                             // open: < folds
		g.move_to(ax + cw, ay - ah); g.line_to(ax, ay); g.line_to(ax + cw, ay + ah);
	}
	g.stroke();
}

// ---- lock: a stage's random lock, drawn like the Slide toggles (pill; lit while on) with a padlock:
// closed while locked, the shackle open and lifted while unlocked.
function paintLock(g, w, h) {
	var on = Number(value) ? 1 : 0;
	var fill = color(on ? "live_control_selection" : "live_control_bg");
	var fg = color(on ? "live_control_fg_on" : "live_control_fg");
	g.set_source_rgba(fill);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.fill();
	g.set_source_rgba(color("live_contrast_frame"));
	g.set_line_width(1);
	pill(g, 0.5, 0.5, w - 1, h - 1);
	g.stroke();
	var bw = 7, bh = Math.max(3, Math.min(5, h - 8));
	var bx = Math.round(w / 2 - bw / 2);
	var by = Math.round(h / 2) - 0.5;                  // body top
	g.set_source_rgba(fg);
	g.rectangle(bx, by, bw, bh);
	g.fill();
	var r = 2.2;
	var cx = bx + bw / 2 + (on ? 0 : 1.5);
	var cy = by - (on ? 0.5 : 1.5) - r + 2;
	g.set_line_width(1.2);
	g.move_to(cx - r, by);
	g.line_to(cx - r, cy);
	g.arc(cx, cy, r, Math.PI, 2 * Math.PI);
	g.line_to(cx + r, on ? by : cy + 0.5);
	g.stroke();
}

// A six-tooth gear filled in col, with a hole in the button's fill colour.
function drawGear(g, cx, cy, R, col, holeCol) {
	var rIn = R * 0.72;
	var teeth = 6;
	g.set_source_rgba(col);
	for (var t = 0; t < teeth; t++) {
		var a = t * 2 * Math.PI / teeth;
		var pts = [[a - 0.42, rIn], [a - 0.2, R], [a + 0.2, R], [a + 0.42, rIn]];
		for (var k = 0; k < pts.length; k++) {
			var px = cx + Math.cos(pts[k][0]) * pts[k][1];
			var py = cy + Math.sin(pts[k][0]) * pts[k][1];
			if (t === 0 && k === 0) g.move_to(px, py); else g.line_to(px, py);
		}
	}
	g.close_path();
	g.fill();
	var hr = R * 0.34;
	g.set_source_rgba(holeCol);
	g.ellipse(cx - hr, cy - hr, 2 * hr, 2 * hr);
	g.fill();
}

// An undo arrow: the top half of a circle from right to left, a short tail on the right, the head
// on the left pointing down (like the common undo sign).
function drawUndo(g, cx, cy, col) {
	var r = 3.2;
	var y0 = cy + 0.5;
	g.set_source_rgba(col);
	g.set_line_width(1.3);
	g.arc(cx, y0, r, Math.PI, 2 * Math.PI);
	g.stroke();
	g.move_to(cx + r, y0);
	g.line_to(cx + r, y0 + 2);
	g.stroke();
	g.move_to(cx - r - 2.2, y0 - 0.6);
	g.line_to(cx - r, y0 + 2.2);
	g.line_to(cx - r + 2.2, y0 - 0.6);
	g.stroke();
}

function pill(g, x, y, w, h) {
	try {
		g.rectangle_rounded(x, y, w, h, h, h);
	} catch (e) {
		g.rectangle(x, y, w, h);
	}
}

// ---- len: the stage's length as 8 cells, its notes drawn in (tied cells) ----------------------
// Cells inside the length are the boxes' grey, cells past it the darkest frame colour, notes the
// text colour; a held note is one block through the whole length. (Until 2026-11-12 the two greys
// were the other way round, so the unused part looked lighter than the length.) Click (or drag along) the cells to set the length.

function list() {
	if (kind !== "len") return;
	var a = Array.prototype.slice.call(arguments);
	if (a.length < 3) return;
	lv.mode = Math.max(0, Math.min(3, Math.round(Number(a[0]) || 0)));
	lv.div = Math.max(1, Math.min(4, Math.round(Number(a[1]) || 1)));
	lv.len = Math.max(1, Math.min(8, Math.round(Number(a[2]) || 1)));
	mgraphics.redraw();
}

// note start cells of the stage, or -1 for one note held through the length
function lenNotes() {
	if (lv.mode === 0) return [];
	if (lv.mode === 3) return -1;
	var step = lv.mode === 1 ? lv.len : lv.div;
	var r = [];
	for (var k = 0; k < lv.len; k += step) r.push(k);
	return r;
}

function paintLen(g, w, h) {
	var cw = (w - 7) / 8;
	var notes = lenNotes();
	for (var k = 0; k < 8; k++) {
		var on = notes !== -1 && notes.indexOf(k) >= 0;
		g.set_source_rgba(color(k >= lv.len ? "live_contrast_frame" : (on ? "live_control_fg" : "live_control_text_bg")));
		g.rectangle(k * (cw + 1), 0, cw, h);
		g.fill();
	}
	if (notes === -1) {
		g.set_source_rgba(color("live_control_fg"));
		g.rectangle(0, 0, lv.len * (cw + 1) - 1, h);
		g.fill();
	}
}

function onclick(x, y) { pickLen(x); }
function ondrag(x, y, but) { if (but) pickLen(x); }

function pickLen(x) {
	if (kind !== "len") return;
	var w = area()[0];
	var k = Math.max(0, Math.min(7, Math.floor(x / w * 8)));
	if (k + 1 === lv.len) return;
	lv.len = k + 1;
	mgraphics.redraw();
	outlet(0, k);
}
