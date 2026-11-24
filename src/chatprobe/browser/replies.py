"""Captura respostas novas do suporte na sessão aberta pelo navegador."""

from difflib import SequenceMatcher
from time import monotonic, sleep
from unicodedata import normalize


LISTEN_FOR_SUPPORT = """
(() => {
    if (window !== window.top) return;
    const messages = [];
    const seen = new Set();
    window.__chatProbeSupportMessages = messages;

    function parse(value) {
        try {
            return typeof value === "string" ? JSON.parse(value) : value;
        } catch {
            return null;
        }
    }

    window.addEventListener("onEmbeddedMessageSent", (event) => {
        const entry = parse(event.detail)?.conversationEntry;
        const role = entry?.sender?.role;
        if (entry?.entryType !== "Message" || typeof role !== "string"
            || !["chatbot", "agent"].includes(role.toLowerCase())) return;

        const payload = parse(entry.entryPayload);
        const id = entry.identifier || payload?.id;
        if (id && seen.has(id)) return;
        if (id) seen.add(id);

        const content = payload?.abstractMessage?.staticContent;
        const text = typeof content?.text === "string" ? content.text.trim() : "";
        messages.push(text || null);
    });
})();
"""


def message_text_key(text):
    return " ".join(normalize("NFKC", text).casefold().split())


class ReplyCapture:
    def __init__(self, page, locator, *, use_events):
        self.page = page
        self.locator = locator
        self.use_events = use_events

    def _events(self):
        return self.page.evaluate("window.__chatProbeSupportMessages ?? []") if self.use_events else []

    def _visible_messages(self, outgoing):
        return [text.strip() for text in self.locator.all_inner_texts()
                if text.strip() and message_text_key(text) not in outgoing]

    def snapshot(self, outgoing):
        return len(self._events()), self._visible_messages(outgoing)

    def _new_replies(self, before, outgoing):
        event_count, visible_before = before
        events = self._events()[event_count:]
        if events and all(events):
            return events
        if event_count and not events:
            return []

        visible = self._visible_messages(outgoing)
        if not visible_before:
            return visible
        matches = SequenceMatcher(
            None, list(map(message_text_key, visible_before)),
            list(map(message_text_key, visible)), autojunk=False,
        ).get_matching_blocks()
        if len(matches) > 1:
            last = matches[-2]
            return visible[last.b + last.size:] or [text for text in events if text]
        return [text for text in events if text]

    def wait_for_reply(self, before, outgoing, timeout_seconds=60, quiet_seconds=3, *, observe=None):
        deadline = monotonic() + timeout_seconds
        last_reply = []
        last_change = monotonic()
        while monotonic() < deadline:
            reply = self._new_replies(before, outgoing)
            now = monotonic()
            if reply != last_reply:
                last_reply = reply
                last_change = now
                if reply and observe is not None:
                    observe(reply)
            elif reply and now - last_change >= quiet_seconds:
                return reply
            sleep(0.25)
        raise SystemExit("O ChatProbe não capturou uma nova resposta do suporte neste turno.")
