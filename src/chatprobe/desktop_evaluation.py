"""Avaliação explícita da seleção do Histórico."""

import json
from collections import Counter
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from src.chatprobe.desktop_preferences import write_text_atomically
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.history_reports import markdown_block
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
                if review.get("criteria"):
                    lines.append("Critérios revisados · " + ", ".join(f"{key}: {value}" for key, value in review["criteria"].items()))
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

    def configuration():
        rubric = json.loads(editor.value())
        if not isinstance(rubric, dict) or not isinstance(rubric.get("criteria"), list):
            raise ValueError("Preencha a lista criteria no JSON.")
        from src.chatprobe.evaluation.engine import Criterion
        criteria = [Criterion(**raw) for raw in rubric["criteria"]]
        for criterion in criteria:
            criterion.validate()
        if not criteria or len({criterion.id for criterion in criteria}) != len(criteria):
            raise ValueError("Informe critérios únicos.")
        if not isinstance(rubric.get("reference", ""), str) or not isinstance(rubric.get("expected", ""), str):
            raise ValueError("A referência e o resultado esperado devem ser texto.")
        threshold = rubric.get("threshold", 80)
        if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
            raise ValueError("A nota mínima deve estar entre 0 e 100.")
        profile_id = labels.get(provider.get())
        if rubric.get("automatic_local") is True and any(item.type == "semantic" for item in criteria):
            raise ValueError("A avaliação automática local exige critérios determinísticos.")
        if rubric.get("automatic_local") is True and profile_id:
            raise ValueError("Selecione Avaliação local para automatizar.")
        rubric["provider_id"] = profile_id
        return rubric, criteria

    def save_configuration():
        try:
            rubric, _ = configuration()
            store.save_rubric(scope.get(), rubric)
            load_rubric()
            output.set_value("Configuração salva.")
        except (ValueError, TypeError, OSError) as error:
            messagebox.showerror("Avaliação", str(error), parent=popup)

    def start():
        nonlocal busy, control
        if busy:
            return
        try:
            rubric, criteria = configuration()
            profile_id = rubric["provider_id"]
            active = catalog.resolve(providers[profile_id]) if profile_id else None
            sends_data = bool(active and rubric.get("reference", "").strip() and
                              any(criterion.type == "semantic" for criterion in criteria))
            if sends_data and not messagebox.askyesno("Avaliação por IA",
                "Enviar referência, prompt e conversa ao provedor selecionado? A operação pode consumir cotas ou créditos.",
                parent=popup):
                return
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
            raw = simpledialog.askstring("Critérios revisados", "JSON opcional com ID e novo resultado, ou deixe vazio.",
                                         parent=popup)
            if raw is None:
                return
            try:
                criteria = json.loads(raw) if raw.strip() else {}
            except ValueError:
                messagebox.showerror("Revisão", "JSON de critérios inválido.", parent=popup)
                return
            view.submit(lambda: store.review(match["id"], classification, reason, criteria),
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
                return {item["test_snapshot"].get("library_prompt_id") or item["prompt_hash"]: item for item in rows}
            left, right = index(first), index(current)
            if not left:
                output.set_value("Sem baseline.")
                return
            rows = {key: regression(left.get(key), right.get(key)) for key in sorted(left.keys() | right.keys())}
            counts = Counter(rows.values())
            brief = "\n".join(f"{REGRESSIONS[key]} · {count}" for key, count in counts.items())
            output.set_value(brief + "\n\n" + "\n".join(f"{key} · {REGRESSIONS[value]}"
                                                            for key, value in rows.items()))
            chart("Regressão da seleção", {REGRESSIONS[key]: count for key, count in counts.items()})
        view.submit(lambda: store.baseline(key), compare, valid=popup.winfo_exists)

    def chart(title, counts):
        dialog = tk.Toplevel(popup)
        dialog.title(title)
        graph = tk.Canvas(dialog, width=470, height=max(120, len(counts) * 34 + 25), background="#1e2939")
        graph.pack(fill="both", expand=True)
        maximum = max(counts.values(), default=1)
        for row, (label, count) in enumerate(counts.items(), 1):
            y = row * 31
            graph.create_text(12, y, text=label, fill="#f1f5f9", anchor="w")
            right = 145 + 270 * count / maximum
            graph.create_rectangle(145, y-9, right, y+9, fill="#2563eb", outline="")
            graph.create_text(right + 6, y, text=str(count), fill="#f1f5f9", anchor="w")

    def dashboard():
        items = latest()
        counts = Counter(LABELS[item.get("review", {}).get("classification", item["classification"])]
                         for item in items)
        counts["Não avaliado"] = len(records) - len(items)
        chart("Avaliação da seleção", counts)

    def export():
        if not selection:
            return
        path = filedialog.asksaveasfilename(parent=popup, defaultextension=".json", confirmoverwrite=True,
                                            filetypes=[("JSON", "*.json"), ("Markdown", "*.md")])
        if path:
            target = Path(path)
            content = json.dumps(selection, ensure_ascii=False, indent=2)
            if target.suffix.lower() == ".md":
                content = "# Avaliações de atendimento\n\n" + markdown_block(content)
            view.submit(lambda: write_text_atomically(target, content), lambda _: None, valid=popup.winfo_exists)

    toolbar = ttk.Frame(popup)
    toolbar.pack(fill="x", padx=8, pady=6)
    for index, (label, callback) in enumerate((
        ("Avaliar", start), ("Salvar critérios", save_configuration), ("Anteriores", recent), ("Revisar", review), ("Exportar", export),
        ("Fixar baseline", lambda: baseline(True)), ("Comparar", lambda: baseline(False)), ("Painel", dashboard),
        ("Pausar/retomar", lambda: (control.pause() if control.ready.is_set() else control.resume()) if control else None),
        ("Parar", lambda: control.stop() if control else None),
    )):
        ttk.Button(toolbar, text=label, command=callback).grid(row=index // 5, column=index % 5, padx=2, pady=2, sticky="ew")

    def close():
        if control:
            control.stop()
        popup.destroy()

    scope.bind("<<ComboboxSelected>>", load_rubric)
    popup.protocol("WM_DELETE_WINDOW", close)
    load_rubric()
    recent()


def evaluate_automatic(history, identifiers):
    store = EvaluationStore(history.store)
    total = 0
    for record in history.details(identifiers):
        rubric = store.load_rubric(selection_scopes([record]))
        if rubric.get("automatic_local") is True and store.for_test(record["id"]) == []:
            if any(c.get("type") == "semantic" for c in rubric.get("criteria", ())):
                continue
            store.save(evaluate(record, rubric))
            total += 1
    return total
