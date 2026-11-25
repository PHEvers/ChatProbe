"""Avaliação explícita da seleção do Histórico."""

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from src.chatprobe.desktop_preferences import write_text_atomically
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.evaluation.engine import evaluate, regression
from src.chatprobe.evaluation.store import EvaluationStore
from src.chatprobe.runner.batch import BatchControl, BatchStopped
from src.chatprobe.runner.consumption import ConsumptionLedger


LABELS = {"passed": "Aprovado", "failed": "Reprovado", "inconclusive": "Inconclusivo"}
REGRESSIONS = {"new": "Novo", "removed": "Removido", "not_comparable": "Não comparável",
               "inconclusive": "Inconclusivo", "regression": "Regressão", "improved": "Melhoria",
               "persistent": "Falha persistente", "stable": "Estável"}


def selection_scopes(records):
    first = records[0]
    scopes = []
    for key, prefix in (("library_prompt_id", "prompt:"), ("library_collection_id", "collection:")):
        value = first.get(key)
        if value and all(record.get(key) == value for record in records):
            scopes.append(prefix + str(value))
    scopes.append("global")
    return scopes


def evaluate_selection(view, records, catalog):
    if not records:
        return
    store = EvaluationStore(view.history.store)
    popup = tk.Toplevel(view)
    popup.title("Avaliação e regressão")
    popup.geometry("850x610")
    scopes = selection_scopes(records)
    providers = dict(catalog.profiles) if catalog else {}
    editor = PromptEditor(popup, title=False)
    editor.pack(fill="both", expand=True, padx=8, pady=4)
    output = PromptEditor(popup, title=False, readonly=True)
    output.pack(fill="both", expand=True, padx=8)
    if view.palette:
        editor.apply_palette(view.palette)
        output.apply_palette(view.palette)
    top = ttk.Frame(popup)
    top.pack(fill="x", before=editor, padx=8, pady=6)
    scope = ttk.Combobox(top, values=scopes, state="readonly", width=27)
    scope.set(scopes[0])
    scope.pack(side="left")
    provider = ttk.Combobox(top, state="readonly", width=38)
    labels = {f"{profile.name} · {profile.model} [{profile.id[:8]}]": identifier
              for identifier, profile in providers.items() if profile.model}
    provider.configure(values=("Avaliação local", *labels))
    provider.pack(side="left", padx=8)
    control = None
    busy = False
    selection = []

    def load_rubric(event=None):
        config = store.load_rubric([scope.get(), *scopes])
        active = config.get("provider_id")
        provider.set(next((label for label, identifier in labels.items() if identifier == active), "Avaliação local"))
        editor.set_value(json.dumps(config, ensure_ascii=False, indent=2))

    def show(items):
        selection[:] = items
        lines = []
        for item in items:
            lines.append(f"{item['test_id']} · {LABELS[item['classification']]} · {item.get('score')} · avaliação {item['id']}")
            if item.get("review"):
                review = item["review"]
                lines.append(f"Revisão humana · {LABELS[review['classification']]} · {review['reason']}")
            if item.get("issue"):
                lines.append(item["issue"])
            for criterion in item["results"]:
                lines.append(f"  {criterion['id']} · {criterion['status']} · {criterion['reason']}")
                for mention in criterion["evidence"]:
                    lines.append(f"    {mention['message_id']} · {mention['quote']}")
        output.set_value("\n".join(lines) or "Nenhuma avaliação registrada.")

    def recent():
        view.submit(lambda: [item for row in records for item in store.for_test(row["id"])],
                    show, valid=popup.winfo_exists)

    def start():
        nonlocal busy, control
        if busy:
            return
        try:
            rubric = json.loads(editor.value())
            if not isinstance(rubric, dict) or not isinstance(rubric.get("criteria"), list):
                raise ValueError("Preencha a lista criteria no JSON.")
            reference = rubric.get("reference", "")
            if not isinstance(reference, str) or not isinstance(rubric.get("expected", ""), str):
                raise ValueError("A referência e o resultado esperado devem ser texto.")
            from src.chatprobe.evaluation.engine import Criterion
            criteria = [Criterion(**raw) for raw in rubric["criteria"]]
            for criterion in criteria:
                criterion.validate()
            if not criteria or len({criterion.id for criterion in criteria}) != len(criteria):
                raise ValueError("Informe critérios únicos.")
            threshold = rubric.get("threshold", 80)
            if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
                raise ValueError("A nota mínima deve estar entre 0 e 100.")
            profile_id = labels.get(provider.get())
            active = catalog.resolve(providers[profile_id]) if profile_id else None
            sends_data = bool(active and reference.strip() and any(criterion.type == "semantic" for criterion in criteria))
            if sends_data and not messagebox.askyesno("Avaliação por IA",
                "Enviar referência, prompt e conversa ao provedor selecionado? A operação pode consumir cotas ou créditos.",
                parent=popup):
                return
            rubric["provider_id"] = profile_id
            rubric = store.save_rubric(scope.get(), rubric)
        except (ValueError, TypeError, OSError) as error:
            messagebox.showerror("Avaliação", str(error), parent=popup)
            return
        busy, control = True, BatchControl()
        output.set_value("Avaliando os testes selecionados...")
        def operation():
            for record in records:
                try:
                    control.checkpoint()
                    result = evaluate(record, rubric, provider=active, ledger=ConsumptionLedger(store.store),
                                      related_profiles=tuple(providers.values()), control=control)
                    store.save(result)
                except BatchStopped:
                    break
            return [item for row in records for item in store.for_test(row["id"])]

        def done(items):
            nonlocal busy
            busy = False
            show(items)
        def failed(message):
            nonlocal busy
            busy = False
            output.set_value(message)
        view.submit(operation, done, valid=popup.winfo_exists, on_error=failed)

    def review():
        if not selection:
            return
        entry = simpledialog.askstring("Revisão", "ID da avaliação (vazio usa a primeira)", parent=popup)
        if entry is None:
            return
        match = next((item for item in selection if item["id"] == entry), None) if entry else selection[0]
        if not match:
            return
        classification = simpledialog.askstring("Revisão", "Classificação: passed, failed ou inconclusive", parent=popup)
        reason = simpledialog.askstring("Revisão", "Justificativa", parent=popup)
        if classification and reason:
            view.submit(lambda: store.review(match["id"], classification, reason),
                        lambda _: recent(), valid=popup.winfo_exists)

    def latest():
        return list({record["test_id"]: record for record in reversed(selection)}.values())

    def baseline(save):
        current = latest()
        if not current:
            return
        if save:
            if not messagebox.askyesno("Baseline", "Definir os resultados atuais como referência?", parent=popup):
                return
            key = scope.get()
            view.submit(lambda: store.baseline(key, current), lambda _: output.set_value("Baseline salva."),
                        valid=popup.winfo_exists)
            return
        key = scope.get()
        def compare(first):
            def index(rows):
                return {item["test_snapshot"].get("library_prompt_id") or item["test_id"]: item for item in rows}
            left, right = index(first), index(current)
            output.set_value("\n".join(f"{key} · {REGRESSIONS[regression(left.get(key),right.get(key))]}"
                                       for key in sorted(left.keys() | right.keys())) if left else "Sem baseline.")
        view.submit(lambda: store.baseline(key), compare, valid=popup.winfo_exists)

    def export():
        if not selection:
            return
        path = filedialog.asksaveasfilename(parent=popup, defaultextension=".json")
        if path:
            write_text_atomically(Path(path), json.dumps(selection, ensure_ascii=False, indent=2))

    toolbar = ttk.Frame(popup)
    toolbar.pack(fill="x", padx=8, pady=6)
    for label, callback in (
        ("Avaliar", start), ("Anteriores", recent), ("Revisar", review), ("Exportar", export),
        ("Fixar baseline", lambda: baseline(True)), ("Comparar", lambda: baseline(False)),
        ("Pausar/retomar", lambda: (control.pause() if control.ready.is_set() else control.resume()) if control else None),
        ("Parar", lambda: control.stop() if control else None),
    ):
        ttk.Button(toolbar, text=label, command=callback).pack(side="left")

    def close():
        if control:
            control.stop()
        popup.destroy()

    scope.bind("<<ComboboxSelected>>", load_rubric)
    popup.protocol("WM_DELETE_WINDOW", close)
    load_rubric()
    recent()
