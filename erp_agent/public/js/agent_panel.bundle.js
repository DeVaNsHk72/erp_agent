// ERP Agent side panel: a resizable panel docked to the right of desk that shows
// the /agent chat in an iframe. Desk content shrinks to make room instead of being covered.

const MIN_WIDTH = 340;
const MAX_WIDTH = 760;
const DEFAULT_WIDTH = 420;
const STORAGE_KEY = "erp_agent_panel";

const SPARKLES_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/><path d="M20 2v4"/><path d="M22 4h-4"/><circle cx="4" cy="20" r="2"/></svg>`;

// Remembered per browser: whether the panel was open and how wide it was
function loadState() {
	try {
		return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
	} catch (e) {
		return {};
	}
}

function saveState(patch) {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...loadState(), ...patch }));
	} catch (e) {
		// storage blocked: the panel still works, it just won't remember
	}
}

const panel = document.createElement("aside");
panel.className = "agent-panel";
panel.setAttribute("aria-label", "ERP Agent");

const resizer = document.createElement("div");
resizer.className = "agent-resizer";
resizer.title = "Drag to resize";

const iframe = document.createElement("iframe");
iframe.title = "ERP Agent chat";

panel.append(resizer, iframe);

const launcher = document.createElement("button");
launcher.className = "agent-launcher";
launcher.type = "button";
launcher.innerHTML = `${SPARKLES_SVG}<span>Ask AI</span>`;
launcher.title = "Ask ERP Agent (Ctrl/Cmd + I)";

document.body.append(panel, launcher);

function setWidth(width) {
	const clamped = Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width)));
	document.documentElement.style.setProperty("--agent-width", `${clamped}px`);
	return clamped;
}

function isOpen() {
	return document.body.classList.contains("agent-open");
}

function setOpen(open) {
	// Load the chat only the first time it is opened
	if (open && !iframe.src) iframe.src = "/agent";
	document.body.classList.toggle("agent-open", open);
	saveState({ open });
	if (open) {
		sendContext();
		iframe.contentWindow?.postMessage({ type: "erp-agent-focus" }, window.location.origin);
	}
	// Desk lists and charts measure their width; let them re-layout
	setTimeout(() => window.dispatchEvent(new Event("resize")), 220);
}

launcher.addEventListener("click", () => setOpen(true));

frappe.ui.keys.add_shortcut({
	shortcut: "ctrl+i",
	action: () => setOpen(!isOpen()),
	description: __("Toggle ERP Agent"),
	ignore_inputs: true,
});

// Drag the left edge to resize. The iframe would swallow mouse events, so it is
// made click-through while dragging.
resizer.addEventListener("pointerdown", (event) => {
	event.preventDefault();
	document.body.classList.add("agent-resizing");
	resizer.setPointerCapture(event.pointerId);

	const onMove = (e) => setWidth(window.innerWidth - e.clientX);
	const onUp = (e) => {
		resizer.releasePointerCapture(e.pointerId);
		resizer.removeEventListener("pointermove", onMove);
		resizer.removeEventListener("pointerup", onUp);
		document.body.classList.remove("agent-resizing");
		const width = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--agent-width"));
		saveState({ width });
		window.dispatchEvent(new Event("resize"));
	};
	resizer.addEventListener("pointermove", onMove);
	resizer.addEventListener("pointerup", onUp);
});

resizer.addEventListener("dblclick", () => saveState({ width: setWidth(DEFAULT_WIDTH) }));

// Tell the chat which desk page the user is on, so "this invoice" has a meaning
function currentContext() {
	const route = frappe.get_route() || [];
	if (route[0] === "Form" && route[1]) return { doctype: route[1], docname: route[2] };
	if (["List", "Report"].includes(route[0]) && route[1]) return { doctype: route[1] };
	return null;
}

function sendContext() {
	iframe.contentWindow?.postMessage(
		{ type: "erp-agent-context", context: currentContext() },
		window.location.origin
	);
}

// Agent Chats (e.g. the daily digest notification) open in the panel, not as a raw form
let pendingChat = null;

function openChatInPanel(chat) {
	pendingChat = chat;
	setOpen(true);
	iframe.contentWindow?.postMessage({ type: "erp-agent-open-chat", chat }, window.location.origin);
}

frappe.router.on("change", () => {
	const route = frappe.get_route() || [];
	if (route[0] === "Form" && route[1] === "Agent Chat" && route[2]) {
		window.history.back();
		openChatInPanel(route[2]);
		return;
	}
	sendContext();
});

// Messages from the chat: it announces itself when loaded, and asks to close
window.addEventListener("message", (event) => {
	if (event.origin !== window.location.origin) return;
	if (event.data?.type === "erp-agent-ready") {
		sendContext();
		// The chat was still loading when a digest link was clicked
		if (pendingChat) iframe.contentWindow?.postMessage({ type: "erp-agent-open-chat", chat: pendingChat }, window.location.origin);
	}
	if (event.data?.type === "erp-agent-chat-opened") pendingChat = null;
	if (event.data?.type === "erp-agent-close") setOpen(false);
	if (event.data?.type === "erp-agent-open-doc" && event.data.doctype && event.data.name) {
		frappe.set_route("Form", event.data.doctype, event.data.name);
	}
});

const saved = loadState();
setWidth(saved.width || DEFAULT_WIDTH);
if (saved.open) setOpen(true);
