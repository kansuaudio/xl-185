// ml185stage.js
// Per-stage tables for the ML-185 note path: probability, note length and slide.
//
// Control rate only. The patch reads these tables with zl.lookup in the timing thread when a
// stage plays a note, so nothing here sits in the timing path. Every stage control (Mode, Div,
// Length, Probability, Slide) and Clock Timing report here; any change re-sends all tables.

autowatch = 0;
inlets = 1;
outlets = 7;

var OUT_PROB = 0;    // 8 values 0..100: chance a stage's note plays
var OUT_DUR = 1;     // 8 values: note length in ticks (480 per quarter note)
var OUT_SLIDE = 2;   // 8 values 0/1: the stage's note slides into the next one
var OUT_LEN = 3;     // 8 values: stage length in steps (R direction)
var OUT_START = 4;   // 8 values: steps before each stage (R direction)
var OUT_CTL = 5;     // routing: "rgate 1|2", "rstages n", "rreset", "keyin 0|1", "keymode 0|1"
var OUT_MOD = 6;     // mod lanes: "moda v1..v8" (0..1, Min..Max applied), "glidea ms" (lanes a-c)

var STEP_TICKS = [60, 120, 240, 480];   // one clock step at Clock Timing 1/32, 1/16, 1/8, 1/4

var mode = [1, 1, 1, 1, 1, 1, 1, 1];      // 0 silent, 1 I (one note), 2 II (repeats), 3 I- (hold)
var div = [1, 1, 1, 1, 1, 1, 1, 1];       // repeat every 1..4 steps (Mode II)
var len = [1, 1, 1, 1, 1, 1, 1, 1];       // stage length in steps, 1..8
var prob = [100, 100, 100, 100, 100, 100, 100, 100];
var slide = [0, 0, 0, 0, 0, 0, 0, 0];
var clockIdx = 1;                          // 1/16
var dirIdx = 0;      // Direction: 0 >, 1 <, 2 ><, 3 R
var seqLen = 8;      // Length knob: stages in use (Stages mode)
var keyIn = 0;
var keyMode = 0;     // 0 Latch, 1 Hold
var lastGate = 0;    // R routing last sent
var LANES = ["a", "b", "c"];       // three mod lanes
var NL = LANES.length;
var modVal = [];     // [lane][stage] 0..100
var modMin = [0, 0, 0];
var modMax = [100, 100, 100];
var modGlide = [0, 0, 0];
for (var li = 0; li < NL; li++) modVal.push([0, 0, 0, 0, 0, 0, 0, 0]);
var lastStages = 0;

var self = this;
// scripting names of the stage controls ("Stage3 Slide"), read after load by refresh()
var NAMES = [["mode", "Mode"], ["div", "Div"], ["len", "Length"], ["prob", "Probability"],
	["slide", "Slide"]];

// "stage <kind> <stage 1..8> <value>". Mode, Div and Length arrive as tab indexes.
function stage(kind, n, v) {
	kind = String(kind);
	if (!setStage(kind, n, v)) return;
	tables();
	if (kind === "mode" || kind === "div" || kind === "len") pushView(clampInt(n, 1, 8) - 1);
}

// The face shows each stage's Mode (a dropdown: rest, once, hold, 1/4, 1/3, 1/2, all) and its
// Len cells with the notes drawn in. Mode, Div and Length stay the stage's parameters underneath;
// the face only shows them (and ml185scale.js writes them when a Mode is chosen).
function modeIndex(i) {
	if (mode[i] === 0) return 0;
	if (mode[i] === 1) return 1;
	if (mode[i] === 3) return 2;
	return 7 - div[i];          // repeats: every 4 -> 3, every 3 -> 4, every 2 -> 5, all -> 6
}

function pushView(i) {
	var n = "Stage" + (i + 1);
	var m = modeIndex(i);
	sendTo(n + " Mode Select", ["set", m]);
	sendTo(n + " Len View", ["list", mode[i], div[i], len[i]]);
}

function sendTo(name, args) {
	try {
		var o = self.patcher.getnamed(name);
		if (o) o.message.apply(o, args);
	} catch (e) {}
}

function setStage(kind, n, v) {
	var i = clampInt(n, 1, 8) - 1;
	switch (kind) {
	case "mode": mode[i] = clampInt(v, 0, 3); break;
	case "div": div[i] = clampInt(v, 0, 3) + 1; break;
	case "len": len[i] = clampInt(v, 0, 7) + 1; break;
	case "prob": prob[i] = clampInt(v, 0, 100); break;
	case "slide": slide[i] = Number(v) ? 1 : 0; break;
	default: return false;
	}
	return true;
}

// After the device has loaded: read every stage control and the clock by scripting name (a
// toggle must never be banged to make it report: a bang flips it).
function refresh() {
	for (var n = 1; n <= 8; n++) {
		for (var k = 0; k < NAMES.length; k++) {
			var v = readValue("Stage" + n + " " + NAMES[k][1]);
			if (v !== null) setStage(NAMES[k][0], n, v);
		}
	}
	var c = readValue("Clock Timing");
	if (c !== null) clockIdx = clampInt(c, 0, 3);
	var v;
	if ((v = readValue("Direction")) !== null) dirIdx = clampInt(v, 0, 3);
	if ((v = readValue("Sequence Length")) !== null) seqLen = clampInt(v, 1, 8);
	if ((v = readValue("Key In")) !== null) keyIn = clampInt(v, 0, 1);
	if ((v = readValue("Key Mode")) !== null) keyMode = clampInt(v, 0, 1);
	for (var l = 0; l < NL; l++) {
		var L = "Mod " + LANES[l].toUpperCase();
		for (var st = 1; st <= 8; st++) {
			if ((v = readValue(L + " Stage" + st)) !== null) modVal[l][st - 1] = clampNum(v, 0, 100);
		}
		if ((v = readValue(L + " Min")) !== null) modMin[l] = clampNum(v, 0, 100);
		if ((v = readValue(L + " Max")) !== null) modMax[l] = clampNum(v, 0, 100);
		if ((v = readValue(L + " Smooth")) !== null) modGlide[l] = clampNum(v, 0, 2000);
		modTable(l);
	}
	lastGate = 0;                // after load: send everything
	tables();
	for (var vi = 0; vi < 8; vi++) pushView(vi);
	routing();
	outlet(OUT_CTL, "keymode", keyMode);
	outlet(OUT_CTL, "keyin", keyIn);
}

function direction(v) { dirIdx = clampInt(v, 0, 3); routing(); }
function seqlen(v) { seqLen = clampInt(v, 1, 8); routing(); }

// R plays whole stages, picked at random among the stages in use. Entering R, or a change of the
// stages in use, restarts the picking.
function routing() {
	var g = dirIdx === 3 ? 2 : 1;
	if (g !== lastGate || seqLen !== lastStages) {
		outlet(OUT_CTL, "rstages", seqLen);
		outlet(OUT_CTL, "rgate", g);
		if (g === 2) outlet(OUT_CTL, "rreset");
		lastGate = g;
		lastStages = seqLen;
	}
}

function readValue(name) {
	try {
		var o = self.patcher.getnamed(name);
		if (!o) return null;
		var v = o.getvalueof();
		if (Object.prototype.toString.call(v) === "[object Array]") v = v[0];
		v = Number(v);
		return isFinite(v) ? v : null;
	} catch (e) {
		return null;
	}
}

// Mod lanes (lane 0..2 = A..C). The value a stage sends is Min + value x (Max - Min), as 0..1 of
// the mapped parameter's range (live.remote~ in normalized mode). Min above Max turns the lane over.
function modval(lane, n, v) {
	var l = clampInt(lane, 0, NL - 1);
	modVal[l][clampInt(n, 1, 8) - 1] = clampNum(v, 0, 100);
	modTable(l);
}
function modmin(lane, v) { var l = clampInt(lane, 0, NL - 1); modMin[l] = clampNum(v, 0, 100); modTable(l); }
function modmax(lane, v) { var l = clampInt(lane, 0, NL - 1); modMax[l] = clampNum(v, 0, 100); modTable(l); }
// lane Smooth (ms on the face; the messages keep their first name, glide)
function modglide(lane, v) {
	var l = clampInt(lane, 0, NL - 1);
	modGlide[l] = clampNum(v, 0, 2000);
	outlet(OUT_MOD, "glide" + LANES[l], modGlide[l]);
}

function modTable(l) {
	var t = [];
	for (var i = 0; i < 8; i++) {
		var x = (modMin[l] + modVal[l][i] / 100 * (modMax[l] - modMin[l])) / 100;
		t.push(Math.round(x * 100000) / 100000);
	}
	outlet(OUT_MOD, "glide" + LANES[l], modGlide[l]);
	outlet(OUT_MOD, ["mod" + LANES[l]].concat(t));
}

function clampNum(v, a, b) {
	v = Number(v);
	if (!isFinite(v)) v = a;
	return Math.max(a, Math.min(b, v));
}

function clock(v) {
	clockIdx = clampInt(v, 0, 3);
	tables();
}

function loadbang() { tables(); }
function bang() { tables(); }

function tables() {
	var d = [];
	var starts = [];
	var at = 0;
	for (var i = 0; i < 8; i++) {
		d.push(noteTicks(i));
		starts.push(at);
		at += len[i];
	}
	outlet(OUT_START, starts);
	outlet(OUT_LEN, len.slice());
	outlet(OUT_SLIDE, slide.slice());
	outlet(OUT_DUR, d);
	outlet(OUT_PROB, prob.slice());
}

// A note lasts one step (once, repeats) or the whole stage (hold), ending one tick before the next
// step, as 1.x did at 1/16 with its gate at 100 %. A sliding note is held for its whole slot (up to
// the next note this stage could play) plus 3/4 of a step, so it is still sounding when the next
// note starts, even with full swing (half a step) and the 10-tick transport poll.
function noteTicks(i) {
	var step = STEP_TICKS[clockIdx];
	var L = len[i];
	if (slide[i]) {
		var slot = mode[i] === 2 ? Math.min(div[i], L) : L;
		return slot * step + Math.round(step * 0.75);
	}
	var span = (mode[i] === 3 ? L : 1) * step;
	return Math.max(1, span - 1);
}

function clampInt(v, a, b) {
	v = Math.round(Number(v));
	if (!isFinite(v)) v = a;
	return Math.max(a, Math.min(b, v));
}
