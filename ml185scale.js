// ml185scale.js
// Scale, tuning and random-range engine for the ML-185 sequencer.
//
// Runs at control rate only. It builds two 128-entry lookup tables
// (note out, pitch bend out) that the note path reads with zl.lookup,
// so nothing here sits in the timing-critical path.
//
// Pitch knobs keep their semitone positions. The played note is the
// nearest pitch of the active scale (ties go down). Microtonal scales
// play the nearest semitone plus a 14-bit pitch bend.

autowatch = 0;
inlets = 1;
outlets = 7;

var OUT_NOTES = 0;   // note table -> zl.lookup right inlet
var OUT_BENDS = 1;   // bend table (0..16383, centre 8192) -> zl.lookup right inlet
var OUT_RANDOM = 2;  // "stage value" pairs for the pitch dials
var OUT_RANDOM_VEL = 3;  // "stage value" pairs for the velocity dials
var OUT_MICRO = 4;   // 1 while a microtonal (pitch bend) scale is active
var OUT_MIDI = 5;    // raw MIDI bytes (bend reset)
var OUT_WIDTH = 6;   // "setwidth n" -> live.thisdevice (settings column fold)

var DIAL_LO = 24;    // pitch dials span -36..+36 around note 60
var DIAL_HI = 96;

// Name, then pitches in cents above the root within one octave.
// The order here is the order of the Scale menu (the build script reads it).
var SCALES = [
	["Chromatic", st([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])],
	["Major", st([0, 2, 4, 5, 7, 9, 11])],
	["Minor", st([0, 2, 3, 5, 7, 8, 10])],
	["Dorian", st([0, 2, 3, 5, 7, 9, 10])],
	["Phrygian", st([0, 1, 3, 5, 7, 8, 10])],
	["Lydian", st([0, 2, 4, 6, 7, 9, 11])],
	["Mixolydian", st([0, 2, 4, 5, 7, 9, 10])],
	["Locrian", st([0, 1, 3, 5, 6, 8, 10])],
	["Harmonic Minor", st([0, 2, 3, 5, 7, 8, 11])],
	["Melodic Minor", st([0, 2, 3, 5, 7, 9, 11])],
	["Phrygian Dominant", st([0, 1, 4, 5, 7, 8, 10])],
	["Hungarian Minor", st([0, 2, 3, 6, 7, 8, 11])],
	["Major Pentatonic", st([0, 2, 4, 7, 9])],
	["Minor Pentatonic", st([0, 3, 5, 7, 10])],
	["Minor Blues", st([0, 3, 5, 6, 7, 10])],
	["Whole Tone", st([0, 2, 4, 6, 8, 10])],
	["Hirajoshi", st([0, 2, 3, 7, 8])],
	["In-Sen", st([0, 1, 5, 7, 10])],
	// Microtonal: played as nearest semitone + pitch bend.
	["Rast ~", [0, 200, 350, 500, 700, 900, 1050]],
	["Bayati ~", [0, 150, 300, 500, 700, 800, 1000]],
	["Sikah ~", [0, 150, 350, 550, 700, 850, 1050]],
	["Just Major ~", [0, 203.91, 386.31, 498.04, 701.96, 884.36, 1088.27]],
	["Just Minor ~", [0, 203.91, 315.64, 498.04, 701.96, 813.69, 1017.6]],
	["Harmonic 8-16 ~", [0, 203.91, 386.31, 551.32, 701.96, 840.53, 968.83, 1088.27]],
	["Slendro 5-EDO ~", [0, 240, 480, 720, 960]],
	["7-EDO ~", [0, 171.43, 342.86, 514.29, 685.71, 857.14, 1028.57]]
];

// Display list for LIVE mode: Live 12's own scale names, then states we show instead
// of a scale. The order here is the order of the display menu (the build script reads it).
var LIVE_NAMES = [
	"Major", "Minor", "Dorian", "Mixolydian", "Lydian", "Phrygian", "Locrian",
	"Whole Tone", "Half-whole Dim.", "Whole-half Dim.", "Minor Blues", "Minor Pentatonic",
	"Major Pentatonic", "Harmonic Minor", "Harmonic Major", "Dorian #4", "Phrygian Dominant",
	"Melodic Minor", "Lydian Augmented", "Lydian Dominant", "Super Locrian", "8-Tone Spanish",
	"Bhairav", "Hungarian Minor", "Hirajoshi", "In-Sen", "Iwato", "Kumoi", "Pelog Selisir",
	"Pelog Tembung", "Messiaen 3", "Messiaen 4", "Messiaen 5", "Messiaen 6", "Messiaen 7",
	"Chromatic", "Custom", "Scale off", "Live tuning", "(other)"
];

function st(a) {
	var r = [];
	for (var i = 0; i < a.length; i++) r.push(a[i] * 100);
	return r;
}

// ---- state -------------------------------------------------------------

var src = 0;          // 0 OFF, 1 LIVE, 2 OWN, 3 SLICES (the Simpler that follows)
var ownRoot = 0;
var ownScale = 2;     // Minor
var bendRange = 2;    // synth pitch bend range, semitones
var rndLo = 36;       // C1 in Live's naming
var rndHi = 60;       // C3
var velLo = 1;        // velocity 0 is a note-off, so the default range starts at 1
var velHi = 127;

// RANDOM ALL: which parts it randomizes (index = checkbox order in the patch).
var RND_KEYS = ["pitch", "vel", "mode", "div", "stlen", "seqlen", "prob", "slide"];
var rndAll = { pitch: 1, vel: 1, mode: 0, div: 0, stlen: 0, seqlen: 0, prob: 0, slide: 0 };   // = checkbox defaults

var liveRoot = 0;
var liveIntervals = null;
var liveMode = 0;
var liveName = "";

var tun = null;       // Live tuning system, null when none is loaded
var tunSig = "none";

var current = null;   // active model, see model()
var lastMicro = 0;

var self = this;      // the js object, for this.patcher
// Face pages: which objects show depends on the strip page, the panel tab, the scale source and
// whether a microtonal scale is on. The build sends the groups at load ("uigroup <group> names...").
var uiGroups = {};    // scripting name -> [group, ...]
var uiHidden = {};    // scripting name -> hidden state last sent
var stripPage = 0;    // 0 Sequencer, 1 Mod
var uiMicro = 0;

var observers = null;
var tunApi = null;
var tunTask = null;

// ---- messages from the patch -------------------------------------------

function source(v) { src = clampInt(v, 0, 3); rebuild(); }
// The face's Scale menu: Off, Live, Slices, then the own scales. It writes Scale (first, so a switch
// to an own scale builds the right table) and Scale Source; updateScaleDisplay shows them back.
var PICK_SOURCE = [0, 1, 3];   // menu items 0..2 -> Scale Source Off, Live, Slices
function scalepick(v) {
	var k = clampInt(v, 0, SCALES.length + 2);
	if (k >= 3) sendInt("Scale", k - 3);
	sendInt("Scale Source", k >= 3 ? 2 : PICK_SOURCE[k]);
}
function root(v) { ownRoot = clampInt(v, 0, 11); rebuild(); }
function scale(v) { ownScale = clampInt(v, 0, SCALES.length - 1); rebuild(); }
function bendrange(v) { bendRange = clampInt(v, 1, 48); rebuild(); }
function lo(v) { rndLo = clampInt(v, 0, 127); }
function hi(v) { rndHi = clampInt(v, 0, 127); }
function vlo(v) { velLo = clampInt(v, 0, 127); }
function vhi(v) { velHi = clampInt(v, 0, 127); }
function rndflag(i, v) {
	var k = RND_KEYS[clampInt(i, 0, RND_KEYS.length - 1)];
	rndAll[k] = Number(v) ? 1 : 0;
}

function loadbang() {
	rebuild();
}

// Called from live.thisdevice: the Live API is only usable after that bang.
function init() {
	try {
		if (!observers) {
			observers = [];
			var props = ["root_note", "scale_intervals", "scale_mode", "scale_name"];
			for (var i = 0; i < props.length; i++) {
				var api = new LiveAPI(onLiveScale, "live_set");
				api.property = props[i];
				observers.push(api);
			}
		}
		readLiveScale();
		readTuning();
		watchDevices();
		if (!tunTask) {
			tunTask = new Task(pollTuning, this);
			tunTask.interval = 1500;
			tunTask.repeat();
		}
	} catch (e) {
		post("ml185scale: Live API init failed: " + e + "\n");
	}
	rebuild();
	applyFold();      // the device is ready now: send its width again
	undoView();
	mutateauto(readValue("Mutate Auto"));            // the stored Auto settings, also if their load
	mutateevery(readValue("Mutate Every"));          // output came before the script was ready
	lanemutateauto(readValue("Lane Mutate Auto"));
	lanemutateevery(readValue("Lane Mutate Every"));
	for (var i = 1; i <= 8; i++) send("Stage" + i + " Lock Icon", "int", locked(i) ? 1 : 0);
}

// ---- random tools ------------------------------------------------------------------------------
// Each tool the face offers (Global, Reset, Mutate, the Pitch / Vel dice, the lane dice, lane Random /
// Reset) first remembers what it can change, for Undo, then skips every locked stage.

function random() { remember(); rndPitch(); }        // the Pitch die
function randomvel() { remember(); rndVel(); }       // the Vel die

function rndPitch() {
	if (!current) rebuild();
	var pool = randomPool();
	if (!pool.length) return;
	for (var i = 1; i <= 8; i++) {
		if (!locked(i)) outlet(OUT_RANDOM, i, pool[Math.floor(Math.random() * pool.length)]);
	}
}

function rndVel() {
	var a = Math.min(velLo, velHi);
	var b = Math.max(velLo, velHi);
	for (var i = 1; i <= 8; i++) {
		if (!locked(i)) outlet(OUT_RANDOM_VEL, i, a + Math.floor(Math.random() * (b - a + 1)));
	}
}

// A lane's die: its 8 knobs to random whole percentages, 0..100.
var LANE_LETTERS = ["A", "B", "C"];
function randomlane(l) { remember(); laneRandom(l); }
function laneRandom(l) {
	var letter = LANE_LETTERS[clampInt(l, 0, LANE_LETTERS.length - 1)];
	for (var i = 1; i <= 8; i++) {
		if (locked(i)) continue;
		try {
			send("Mod " + letter + " Stage" + i, "float", Math.floor(Math.random() * 101));
		} catch (e) {
			post("ml185scale: cannot set lane " + letter + ": " + e + "\n");
		}
	}
}

// The Mod page's Random and Reset: all three lanes' knobs, nothing else (Min,
// Max, Smooth and the mapping stay).
function randomlanes() {
	remember();
	for (var l = 0; l < LANE_LETTERS.length; l++) laneRandom(l);
}
function resetlanes() {
	remember();
	for (var l = 0; l < LANE_LETTERS.length; l++) {
		for (var i = 1; i <= 8; i++) {
			if (locked(i)) continue;
			try {
				send("Mod " + LANE_LETTERS[l] + " Stage" + i, "float", 0);
			} catch (e) {
				post("ml185scale: cannot reset lane " + LANE_LETTERS[l] + ": " + e + "\n");
			}
		}
	}
}

// Global: the ticked parts of every unlocked stage. Stage tabs and the Length knob are set by
// scripting name; "int" makes them output, so the sequencer reacts as if they had been clicked.
function randomall() {
	remember();
	var free = freeStages(8);
	if (rndAll.pitch) rndPitch();
	if (rndAll.vel) rndVel();
	if (rndAll.mode) randomStages("Mode", 4, free);     // rest, once, repeat, hold
	if (rndAll.div) randomStages("Div", 4, free);       // every 1..4 steps
	if (rndAll.stlen) randomStages("Length", 8, free);  // 1..8 clock steps
	if (rndAll.seqlen) sendInt("Sequence Length", 1 + Math.floor(Math.random() * 8));
	for (var k = 0; k < free.length; k++) {
		if (rndAll.prob) sendInt("Stage" + free[k] + " Probability", randProb());
		if (rndAll.slide) sendInt("Stage" + free[k] + " Slide", randSlide());
	}
}

// Random Probability: 25, 50, 75 or 100 %, never 0 (rest already silences a stage). Random Slide:
// on for about one stage in four, the way 303 lines use it.
var PROB_STEPS = [25, 50, 75, 100];
var SLIDE_CHANCE = 0.25;
function randProb() { return PROB_STEPS[Math.floor(Math.random() * PROB_STEPS.length)]; }
function randSlide() { return Math.random() < SLIDE_CHANCE ? 1 : 0; }

// Reset (the mirror of Global): the parts ticked for Global go back to a fresh
// device's values. Pitch and velocity take the same way as Random; the stage tabs and Length by name.
// The values must match the parameters' initial values.
var RESET = { pitch: 0, vel: 120, mode: 1, div: 0, stlen: 0, seqlen: 8, prob: 100, slide: 0 };
function resetall() {
	remember();
	for (var i = 1; i <= 8; i++) {
		if (locked(i)) continue;
		if (rndAll.pitch) outlet(OUT_RANDOM, i, RESET.pitch);
		if (rndAll.vel) outlet(OUT_RANDOM_VEL, i, RESET.vel);
		if (rndAll.mode) sendInt("Stage" + i + " Mode", RESET.mode);
		if (rndAll.div) sendInt("Stage" + i + " Div", RESET.div);
		if (rndAll.stlen) sendInt("Stage" + i + " Length", RESET.stlen);
		if (rndAll.prob) sendInt("Stage" + i + " Probability", RESET.prob);
		if (rndAll.slide) sendInt("Stage" + i + " Slide", RESET.slide);
	}
	if (rndAll.seqlen) sendInt("Sequence Length", RESET.seqlen);
}

// The face's Mode dropdown per stage (rest, once, hold, 1/4, 1/3, 1/2, all)
// writes the stage's two parameters, Mode (tab 0 rest, 1 one note, 2 repeats, 3 hold) and, for the
// repeat choices, Div (tab 0..3 = every 1..4). Div first, so a stage switching to repeats starts
// with the right interval.
var PICK_MODE = [0, 1, 3, 2, 2, 2, 2];
var PICK_DIV = [-1, -1, -1, 3, 2, 1, 0];
function modepick(n, v) {
	var i = clampInt(n, 1, 8);
	var k = clampInt(v, 0, 6);
	if (PICK_DIV[k] >= 0) sendInt("Stage" + i + " Div", PICK_DIV[k]);
	sendInt("Stage" + i + " Mode", PICK_MODE[k]);
}

// Mutate: ONE random stage among the stages in use (the first "Sequence Length")
// that are not locked gets new values for the parts ticked for Global (Seq Length is not a stage
// part, so Mutate leaves it alone). Same ranges and scale as Global. The Mod page has its own Mutate:
// one such stage gets new values on all three lanes. One undo step each; nothing to pick or no part
// ticked, no step (Auto would otherwise fill the history with empty steps).
function mutate() {
	var i = stagePartsTicked() ? pickStage() : 0;
	if (!i) return;
	remember();
	mutateStage(i);
}
function lanemutate() {
	var i = pickStage();
	if (!i) return;
	remember();
	mutateLanes(i);
}
function stagePartsTicked() {
	return rndAll.pitch || rndAll.vel || rndAll.mode || rndAll.div || rndAll.stlen || rndAll.prob || rndAll.slide;
}
function pickStage() {               // 0 when every stage in use is locked
	var free = freeStages(clampInt(readValue("Sequence Length"), 1, 8));
	return free.length ? free[Math.floor(Math.random() * free.length)] : 0;
}
function mutateStage(i) {
	if (!current) rebuild();
	var pool = randomPool();
	var a = Math.min(velLo, velHi);
	var b = Math.max(velLo, velHi);
	if (rndAll.pitch && pool.length) outlet(OUT_RANDOM, i, pool[Math.floor(Math.random() * pool.length)]);
	if (rndAll.vel) outlet(OUT_RANDOM_VEL, i, a + Math.floor(Math.random() * (b - a + 1)));
	if (rndAll.mode) sendInt("Stage" + i + " Mode", Math.floor(Math.random() * 4));
	if (rndAll.div) sendInt("Stage" + i + " Div", Math.floor(Math.random() * 4));
	if (rndAll.stlen) sendInt("Stage" + i + " Length", Math.floor(Math.random() * 8));
	if (rndAll.prob) sendInt("Stage" + i + " Probability", randProb());
	if (rndAll.slide) sendInt("Stage" + i + " Slide", randSlide());
}
function mutateLanes(i) {           // whole percentages, like the lane dice
	for (var l = 0; l < LANE_LETTERS.length; l++) {
		try {
			send("Mod " + LANE_LETTERS[l] + " Stage" + i, "float", Math.floor(Math.random() * 101));
		} catch (e) {
			post("ml185scale: cannot mutate lane " + LANE_LETTERS[l] + ": " + e + "\n");
		}
	}
}

// Auto: each page's Mutate runs by itself every 4, 8 or 16 bars. The patch sends
// "barend <bar>" once per bar, at the start of its last 16th, so the next bar starts changed. Bars are
// counted from the song position at the first bar end after Play, then one per bar end: with Play from
// bar 1 the changes come before bars 5, 9, 13, and a loop shorter than the interval still gets them.
// Stop starts the count again.
var EVERY = [4, 8, 16];
var autoOn = [0, 0];                 // Sequencer page, Mod page
var autoEvery = [4, 4];
var barsPlayed = -1;                 // -1: take the song position at the next bar end
var EVERY_NAMES = ["Mutate Every", "Lane Mutate Every"];
function mutateauto(v) { setAuto(0, v); }
function mutateevery(v) { autoEvery[0] = EVERY[clampInt(v, 0, EVERY.length - 1)]; }
function lanemutateauto(v) { setAuto(1, v); }
function lanemutateevery(v) { autoEvery[1] = EVERY[clampInt(v, 0, EVERY.length - 1)]; }
// 4 / 8 / 16 is grey while its Auto is off, like Scale Root: an inactive tab takes no
// clicks. Switching Auto on reads the interval again, in case it changed without telling the script.
function setAuto(k, v) {
	autoOn[k] = v ? 1 : 0;
	var e = readValue(EVERY_NAMES[k]);
	if (e !== null) autoEvery[k] = EVERY[clampInt(e, 0, EVERY.length - 1)];
	send(EVERY_NAMES[k], "active", autoOn[k]);
}
function barend(bar) {
	barsPlayed = barsPlayed < 0 ? Math.floor(Number(bar)) : barsPlayed + 1;
	if (!(barsPlayed >= 1)) return;
	var s = autoOn[0] && barsPlayed % autoEvery[0] === 0 && stagePartsTicked() ? pickStage() : 0;
	var l = autoOn[1] && barsPlayed % autoEvery[1] === 0 ? pickStage() : 0;
	if (!s && !l) return;
	remember();                      // one undo step, also when both pages mutate on this bar
	if (s) mutateStage(s);
	if (l) mutateLanes(l);
}

// ---- locks and Undo ----------------------------------------------------------------------------
// A locked stage (its "StageN Lock" toggle, the row under Slide) keeps everything in its column:
// every random and reset tool skips it, lane values included. Seq Length is not a stage value.
function locked(i) { return readValue("Stage" + i + " Lock") === 1; }
function freeStages(m) {
	var a = [];
	for (var i = 1; i <= m; i++) if (!locked(i)) a.push(i);
	return a;
}

// Undo: Live's undo takes one control per Cmd+Z and its API cannot group changes,
// so each random tool first remembers every value it can change, and the Undo arrow writes back the
// ones that differ, in one click. Each press goes one random tool further back, up to HISTORY_MAX.
var HISTORY_MAX = 32;
var history = [];     // what each random tool found before it ran, oldest first
function snapNames() {
	var n = ["Sequence Length"];
	for (var i = 1; i <= 8; i++) {
		n.push("PitchDial" + i, "VelocityDial" + i, "Stage" + i + " Mode", "Stage" + i + " Div",
			"Stage" + i + " Length", "Stage" + i + " Probability", "Stage" + i + " Slide");
		for (var l = 0; l < LANE_LETTERS.length; l++) n.push("Mod " + LANE_LETTERS[l] + " Stage" + i);
	}
	return n;
}
function remember() {
	var names = snapNames();
	var snap = {};
	for (var k = 0; k < names.length; k++) {
		var v = readValue(names[k]);
		if (v !== null) snap[names[k]] = v;
	}
	history.push(snap);
	if (history.length > HISTORY_MAX) history.shift();
	undoView();
}
function undo() {
	if (!history.length) return;
	var s = history.pop();
	for (var name in s) {
		if (!s.hasOwnProperty(name)) continue;
		var now = readValue(name);
		if (now === null || Math.abs(now - s[name]) < 1e-6) continue;
		try {
			send(name, /^Mod /.test(name) ? "float" : "int", s[name]);   // lane knobs are fractional
		} catch (e) {
			post("ml185scale: cannot restore " + name + ": " + e + "\n");
		}
	}
	undoView();
}
function undoView() { send("Undo Button", "enabled", history.length ? 1 : 0); }

function readValue(name) {
	try {
		var o = ui(name);
		if (!o) return null;
		var v = o.getvalueof();
		if (isArr(v)) v = v[0];
		v = Number(v);
		return isFinite(v) ? v : null;
	} catch (e) {
		return null;
	}
}

function randomStages(kind, n, stages) {
	for (var k = 0; k < stages.length; k++) sendInt("Stage" + stages[k] + " " + kind, Math.floor(Math.random() * n));
}

function sendInt(name, v) {
	try {
		send(name, "int", v);
	} catch (e) {
		post("ml185scale: cannot set " + name + ": " + e + "\n");
	}
}

// Transport stop and device off: pitch bend back to centre while a microtonal scale is on, so
// clip or Thru notes to the synth are not left bent. The next note sends its own bend first.
// Auto Mutate's bar count starts again.
function stopped() {
	if (lastMicro) outlet(OUT_MIDI, [224, 0, 64]);
	barsPlayed = -1;                 // Auto counts bars again from the next Play
}

function notifydeleted() {
	if (tunTask) {
		tunTask.cancel();
		tunTask.freepeer();
		tunTask = null;
	}
}

// ---- Live scale and tuning ---------------------------------------------

function onLiveScale() {
	readLiveScale();
	rebuild();
}

function readLiveScale() {
	if (!observers || !observers.length) return;
	var s = observers[0];
	try {
		liveRoot = clampInt(first(s.get("root_note")), 0, 11);
		liveIntervals = numbers(s.get("scale_intervals"));
		liveMode = first(s.get("scale_mode")) ? 1 : 0;
		liveName = asArray(s.get("scale_name")).join(" ");
	} catch (e) {
		liveIntervals = null;
	}
}

function pollTuning() {
	var before = tunSig;
	readTuning();
	if (tunSig !== before) rebuild();
}

// Live maps MIDI note m to tuning step (lowest step + m). The tuning repeats
// every pseudo-octave of n steps; note_tunings holds the cents of each step.
function readTuning() {
	var t = null;
	try {
		if (!tunApi) tunApi = new LiveAPI(nop, "live_set tuning_system");
		else tunApi.goto("live_set tuning_system");
		if (Number(tunApi.id) !== 0) {
			t = { ok: false };
			var cents = numbers(dictValue(tunApi.get("note_tunings"), "note_tunings"));
			var low = dictValue(tunApi.get("lowest_note"), "lowest_note");
			var ref = dictValue(tunApi.get("reference_pitch"), "reference_pitch");
			var pseudo = first(tunApi.get("pseudo_octave_in_cents"));
			if (cents && cents.length && low && ref && ref.frequency > 0) {
				var n = cents.length;
				t.n = n;
				t.cents = cents;
				t.pseudo = pseudo > 0 ? pseudo : 1200;
				t.low = Number(low.octave) * n + Number(low.index_in_octave);
				t.refHz = Number(ref.frequency);
				t.refCents = stepCents(t, Number(ref.octave) * n + Number(ref.index_in_octave));
				t.ok = isFinite(t.low) && isFinite(t.refCents);
			}
		}
	} catch (e) {
		t = null;
	}
	tun = t;
	tunSig = t ? JSON.stringify(t) : "none";
}

function stepCents(t, step) {
	var o = Math.floor(step / t.n);
	return o * t.pseudo + t.cents[step - o * t.n];
}

function tunedHz(m) {
	return tun.refHz * Math.pow(2, (stepCents(tun, tun.low + m) - tun.refCents) / 1200);
}

function hz12(note) {
	return 440 * Math.pow(2, (note - 69) / 12);
}

// ---- model and tables --------------------------------------------------

// pass: notes go through unchanged. Otherwise cents/root define the scale.
// LIVE passes notes through when Live's Scale Mode is off or a tuning system is loaded.
function model() {
	if (src === 3) return { pass: false, slices: true };
	if (src === 1) {
		if (tun || !liveMode || !liveIntervals || !liveIntervals.length) return { pass: true };
		return { pass: false, root: liveRoot, cents: st(liveIntervals) };
	}
	if (src === 2) return { pass: false, root: ownRoot, cents: SCALES[ownScale][1] };
	return { pass: true };
}

function rebuild() {
	var m = model();
	var notes = [];
	var bends = [];
	var micro = 0;
	for (var p = 0; p < 128; p++) {
		if (m.pass || m.slices) {
			notes.push(m.slices ? sliceNote(p) : p);
			bends.push(8192);
			continue;
		}
		var c = snapCents(p * 100, m.root * 100, m.cents);
		var n = Math.floor(c / 100 + 0.5);
		var b = c - n * 100;
		if (Math.abs(b) > 0.01) micro = 1;
		notes.push(n);
		bends.push(bendValue(b));
	}
	current = m;
	outlet(OUT_BENDS, bends);
	outlet(OUT_NOTES, notes);
	outlet(OUT_MICRO, micro);
	uiMicro = micro;
	refreshUI();
	updateScaleDisplay();
	if (lastMicro && !micro) outlet(OUT_MIDI, [224, 0, 64]);
	lastMicro = micro;
}

// ---- Slices: the Simpler that follows ----------------------------------------------------------
// With the Scale menu on Slices, notes stay inside the slices of the first Simpler after this device
// (same track or chain) while it is in Slicing mode: Simpler plays slice 1 on C1 (note 36), so notes
// from C1 to C1 + count - 1 pass and the others wrap around. No Simpler, or not slicing: count 0 and
// the notes pass unchanged. Live API: SimplerDevice.playback_mode 2 = Slicing, Sample.slices (Live 11+).
var SLICE_BASE = 36;
var slices = 0;
var sl = { parent: "", devApi: null, path: "", modeApi: null, sampleApi: null, slicesApi: null };

function sliceNote(p) {
	if (slices <= 0) return p;
	var k = ((p - SLICE_BASE) % slices + slices) % slices;
	return SLICE_BASE + k;
}

function myPlace() {               // ["live_set tracks 2", 1] for "live_set tracks 2 devices 1"
	var me = new LiveAPI(nop, "this_device");
	var path = String(me.unquotedpath || me.path || "").replace(/"/g, "");
	var m = path.match(/^(.*) devices (\d+)$/);
	return m ? [m[1], Number(m[2])] : null;
}

function watchDevices() {
	try {
		var place = myPlace();
		if (!place) { watchSimpler(""); return; }
		if (place[0] !== sl.parent || !sl.devApi) {
			sl.parent = place[0];
			sl.devApi = new LiveAPI(onDevices, sl.parent);
			sl.devApi.property = "devices";
		}
		scanDevices();
	} catch (e) {
		post("ml185scale: cannot look for a Simpler: " + e + "\n");
		setSlices(0);
	}
}
function onDevices() { scanDevices(); }
function scanDevices() {
	try {
		var place = myPlace();
		if (!place || place[0] !== sl.parent) { watchSimpler(""); return; }
		var count = new LiveAPI(nop, sl.parent).getcount("devices");
		var found = "";
		for (var i = place[1] + 1; i < count && !found; i++) {
			var path = sl.parent + " devices " + i;
			if (String(asArray(new LiveAPI(nop, path).get("class_name"))[0]) === "OriginalSimpler") found = path;
		}
		watchSimpler(found);
	} catch (e) {
		post("ml185scale: cannot look for a Simpler: " + e + "\n");
		setSlices(0);
	}
}
function watchSimpler(path) {
	if (path !== sl.path || (path && !sl.modeApi)) {
		sl.path = path;
		sl.modeApi = sl.sampleApi = sl.slicesApi = null;
		if (path) {
			sl.modeApi = new LiveAPI(onSimpler, path);
			sl.modeApi.property = "playback_mode";
			sl.sampleApi = new LiveAPI(onSample, path);
			sl.sampleApi.property = "sample";
		}
	}
	readSlices();
}
function onSimpler() { readSlices(); }
function onSample() { sl.slicesApi = null; readSlices(); }
function readSlices() {
	var n = 0;
	try {
		if (sl.path && sl.modeApi && first(sl.modeApi.get("playback_mode")) === 2) {
			if (!sl.slicesApi) {
				sl.slicesApi = new LiveAPI(onSimpler, sl.path + " sample");
				sl.slicesApi.property = "slices";
			}
			if (Number(sl.slicesApi.id) !== 0) n = numbers(sl.slicesApi.get("slices")).length;
		}
	} catch (e) {
		n = 0;
	}
	setSlices(n);
}
function setSlices(n) {
	n = Math.max(0, Math.min(127, Math.round(Number(n) || 0)));
	if (n === slices) return;
	slices = n;
	if (src === 3) rebuild(); else updateScaleDisplay();
}

// ---- panel: objects addressed by scripting name -------------------------

function ui(name) {
	return self.patcher.getnamed(name);
}

function setHidden(name, hidden) {
	var o = ui(name);
	if (o) o.hidden = hidden ? 1 : 0;
}

function send(name, msg, v) {
	var o = ui(name);
	if (o) o.message(msg, v);
}

function uigroup(g) {
	g = String(g);
	for (var i = 1; i < arguments.length; i++) {
		var name = String(arguments[i]);
		var gs = uiGroups[name] || (uiGroups[name] = []);
		if (gs.indexOf(g) < 0) gs.push(g);
	}
	refreshUI();
}

// The right panel follows the sequencer's tab: Random beside the stages, and beside the Mod
// lanes each lane's Map / Min / Max / Smooth, so a lane's knobs and settings are in view together.
function strippage(v) {
	stripPage = clampInt(v, 0, 1);
	refreshUI();
}

// Bend Range shows only with a microtonal scale; in LIVE mode the OWN Root/Scale menus give way
// to a greyed pair that shows the project's scale (the OWN values are kept for switching back).
function groupOn(g) {
	switch (g) {
	case "steps": return stripPage === 0;       // Sequencer tab: Mode, Len
	case "note": return stripPage === 0;        // Sequencer tab: Prob, Gate, Slide
	case "mod": return stripPage === 1;
	case "notmod": return stripPage !== 1;      // pitch knobs and velocity
	case "tab:random": return stripPage === 0;  // right panel: Random
	case "tab:mod": return stripPage === 1;     // right panel: lane settings
	case "own": return src === 0 || src === 2;
	case "live": return src === 1;
	case "slices": return src === 3;
	case "micro": return uiMicro === 1;
	case "left": return folded === 0;           // the settings column, hidden while folded
	}
	return true;
}

// ---- settings column fold ------------------------------------------------
// The fold button (a stored parameter) folds the left column away: its controls hide (group "left"),
// everything right of it moves left by FOLD_DX and the device narrows (live.thisdevice setwidth).
var FOLD_X = 178;     // the strip starts right of this (presentation x)
var FOLD_DX = 174;    // the strip's left edge goes from 180 to 6
var DEVICE_W = 786;
var folded = 0;
var shiftList = null; // [[object, x, y, w, h], ...] the original rects right of FOLD_X
var shifted = 0;

function fold(v) {    // 0 open, 1 folded
	folded = Number(v) ? 1 : 0;
	applyFold();
}

function firstNum(v) { return isArr(v) ? Number(v[0]) : Number(v); }

function collectShift() {
	shiftList = [];
	var o = self.patcher.firstobject;
	while (o) {
		try {
			if (firstNum(o.getboxattr("presentation")) === 1) {
				var r = o.getboxattr("presentation_rect");
				if (r && r.length >= 4 && Number(r[0]) >= FOLD_X) {
					shiftList.push([o, Number(r[0]), Number(r[1]), Number(r[2]), Number(r[3])]);
				}
			}
		} catch (e) {}
		o = o.nextobject;
	}
}

function applyFold() {
	try {
		if (!shiftList) collectShift();
		if (folded && !shiftList.length) folded = 0;   // nothing can move: stay open rather than crop the face
		if (folded !== shifted) {
			var dx = folded ? -FOLD_DX : 0;
			for (var i = 0; i < shiftList.length; i++) {
				var s = shiftList[i];
				s[0].setboxattr("presentation_rect", s[1] + dx, s[2], s[3], s[4]);
			}
			shifted = folded;
		}
		refreshUI();
		send("Settings Fold Button", "int", folded);   // the drawn button shows the state (also after load)
		outlet(OUT_WIDTH, "setwidth", folded ? DEVICE_W - FOLD_DX : DEVICE_W);
	} catch (e) {
		post("ml185scale: cannot fold the settings: " + e + "\n");
	}
}

function refreshUI() {
	try {
		for (var name in uiGroups) {
			if (!uiGroups.hasOwnProperty(name)) continue;
			var gs = uiGroups[name];
			var on = true;
			for (var i = 0; i < gs.length; i++) {
				if (!groupOn(gs[i])) { on = false; break; }
			}
			var hid = on ? 0 : 1;
			if (uiHidden[name] !== hid) {
				setHidden(name, hid);
				uiHidden[name] = hid;
			}
		}
	} catch (e) {
		post("ml185scale: cannot show/hide controls: " + e + "\n");
	}
}

function updateScaleDisplay() {
	try {
		send("Scale Select", "set", src === 2 ? 3 + ownScale : (src === 3 ? 2 : src));
		send("Scale Root", "active", src === 2 ? 1 : 0);   // the root only matters for an own scale
		send("Slice Count Display", "int", slices);
		if (src === 1) {
			send("Live Root Display", "set", liveRoot);
			send("Live Scale Display", "set", liveScaleIndex());
			send("Live Root Display", "active", 0);
			send("Live Scale Display", "active", 0);
		}
	} catch (e) {
		post("ml185scale: cannot update the scale display: " + e + "\n");
	}
}

function liveScaleIndex() {
	var name = tun ? "Live tuning" : (!liveMode ? "Scale off" : liveName);
	var i = LIVE_NAMES.indexOf(name);
	return i >= 0 ? i : LIVE_NAMES.indexOf("(other)");
}

// Nearest scale pitch (absolute cents, note 0 = 0) to target t; ties go down.
// Only pitches that round to MIDI notes 0..127 are candidates.
function snapCents(t, r, degs) {
	var best = null;
	var bestD = 1e9;
	var k0 = Math.floor((t - r) / 1200);
	for (var k = k0 - 1; k <= k0 + 1; k++) {
		for (var i = 0; i < degs.length; i++) {
			var c = r + k * 1200 + degs[i];
			if (c < -50 || c >= 12750) continue;
			var d = Math.abs(c - t);
			if (d < bestD - 1e-6 || (Math.abs(d - bestD) <= 1e-6 && c < best)) {
				best = c;
				bestD = d;
			}
		}
	}
	return best === null ? t : best;
}

function bendValue(cents) {
	var v = 8192 + Math.round(cents / (bendRange * 100) * 8192);
	return Math.max(0, Math.min(16383, v));
}

// Dial values (-36..36) Random may pick. Each distinct played pitch inside
// the range counts once, so every scale note is equally likely.
function randomPool() {
	var a = Math.min(rndLo, rndHi);
	var b = Math.max(rndLo, rndHi);
	var pool = [];
	var s;
	if (current.slices && slices > 0) {           // every slice once, whatever Note range says
		for (s = SLICE_BASE; s < SLICE_BASE + slices && s <= DIAL_HI; s++) {
			if (s >= DIAL_LO) pool.push(s - 60);
		}
		return pool;
	}
	if (current.pass && tun && tun.ok) {
		// Live retunes every note: match the range by pitch, not note number.
		var fa = hz12(a) * 0.9999;
		var fb = hz12(b) * 1.0001;
		for (s = DIAL_LO; s <= DIAL_HI; s++) {
			var f = tunedHz(s);
			if (f >= fa && f <= fb) pool.push(s - 60);
		}
		if (pool.length) return pool;
	}
	var seen = {};
	for (s = DIAL_LO; s <= DIAL_HI; s++) {
		var c = current.pass ? s * 100 : snapCents(s * 100, current.root * 100, current.cents);
		var d = Math.abs(c - s * 100);
		var key = String(Math.round(c * 1000));
		if (!seen.hasOwnProperty(key) || d < seen[key].d) seen[key] = { s: s, c: c, d: d };
	}
	for (var k in seen) {
		if (!seen.hasOwnProperty(k)) continue;
		var e = seen[k];
		if (e.c >= a * 100 - 0.5 && e.c <= b * 100 + 0.5) pool.push(e.s - 60);
	}
	pool.sort(function (x, y) { return x - y; });
	return pool;
}

// ---- helpers -----------------------------------------------------------

function nop() {}

function clampInt(v, a, b) {
	v = Math.round(Number(v));
	if (!isFinite(v)) v = a;
	return Math.max(a, Math.min(b, v));
}

function isArr(r) {
	return Object.prototype.toString.call(r) === "[object Array]";
}

function asArray(r) {
	if (r === undefined || r === null) return [];
	return isArr(r) ? r : [r];
}

function first(r) {
	var a = asArray(r);
	return a.length ? Number(a[0]) : 0;
}

function numbers(r) {
	var a = asArray(r);
	var out = [];
	for (var i = 0; i < a.length; i++) {
		var x = Number(a[i]);
		if (isFinite(x)) out.push(x);
	}
	return out;
}

// Dictionary-typed Live API properties may come back as a dict name, as
// JSON text or as plain values. Returns the value under key when wrapped.
function dictValue(r, key) {
	var a = asArray(r);
	if (a.length && a[0] === key) a = a.slice(1);
	var obj = null;
	if (a.length >= 2 && a[0] === "dictionary") {
		try {
			obj = JSON.parse(new Dict(String(a[1])).stringify());
		} catch (e) {
			obj = null;
		}
	} else {
		var s = a.join(" ");
		var c = s.charAt(0);
		if (c === "{" || c === "[") {
			try {
				obj = JSON.parse(s);
			} catch (e2) {
				obj = null;
			}
		} else {
			obj = a;
		}
	}
	if (obj && typeof obj === "object" && !isArr(obj) && obj.hasOwnProperty(key)) obj = obj[key];
	return obj;
}
