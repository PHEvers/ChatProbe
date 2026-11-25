"""Diálogo de parâmetros do motor de execução."""

import tkinter as tk
from dataclasses import asdict
from tkinter import simpledialog, ttk

from src.chatprobe.runner.consumption import ExecutionOptions


class ExecutionDialog(simpledialog.Dialog):
    def __init__(self, parent, options):
        self.options = options
        super().__init__(parent, "Controle de execução")

    def body(self, parent):
        values = asdict(self.options)
        labels = (
            ("interaction_seconds", "Entre interações (segundos)"),
            ("test_minutes", "Entre testes da fila (minutos)"),
            ("test_seconds", "Entre testes da fila (segundos adicionais)"),
            ("queue_quota", "Limites da execução (RPM RPD TPM TPD)"),
            ("retries", "Novas tentativas automáticas (0–10)"),
            ("max_wait", "Espera máxima por cota e recuperação (segundos)"),
        )
        values["test_minutes"], values["test_seconds"] = divmod(values["test_seconds"], 60)
        self.fields = {}
        for row, (key, label) in enumerate(labels):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            field = ttk.Entry(parent)
            field.insert(0, str(values[key]))
            field.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            self.fields[key] = field
        self.error = tk.StringVar()
        ttk.Label(parent, textvariable=self.error, wraplength=360).grid(row=len(labels), columnspan=2)
        return self.fields["interaction_seconds"]

    def validate(self):
        fields = {key: field.get().strip() for key, field in self.fields.items()}
        try:
            minutes, seconds = float(fields["test_minutes"]), float(fields["test_seconds"])
            if minutes < 0 or not 0 <= seconds < 60:
                raise ValueError
            self.result = ExecutionOptions(
                float(fields["interaction_seconds"]), 60 * minutes + seconds,
                fields["queue_quota"], int(fields["retries"]), float(fields["max_wait"]),
            )
        except ValueError:
            self.error.set("Confira os intervalos, o número de tentativas e os quatro limites inteiros.")
            return False
        return True
