"""Interface local para executar roteiros do ChatProbe."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from src.chatprobe.desktop_preferences import load_preferences

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/prompts/cupom-nao-impresso.json"
RUNNER = ROOT / "src/chatprobe/browser/smoke.py"


def run_scenario(scenario_text):
    with TemporaryDirectory(prefix="chatprobe-") as directory:
        scenario = Path(directory) / "scenario.json"
        report = Path(directory) / "report.json"
        scenario.write_text(scenario_text, encoding="utf-8")
        process = subprocess.run(
            [
                sys.executable, "-X", "utf8", str(RUNNER), load_preferences().chat_url,
                "--scenario", str(scenario), "--output", str(report),
                "--non-interactive",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        details = "\n".join(part.strip() for part in (process.stdout, process.stderr)
                            if part.strip())
        if process.returncode or not report.is_file():
            return None, details
        return report.read_text(encoding="utf-8"), details


st.set_page_config(page_title="ChatProbe", page_icon="💬")
st.title("ChatProbe")
st.caption("Testes do AutoSystem em homologação")

with st.form("scenario"):
    scenario_text = st.text_area(
        "Roteiro JSON",
        value=EXAMPLE.read_text(encoding="utf-8"),
        height=260,
        help="Edite o exemplo ou cole o JSON preparado para o seu cenário.",
    )
    submitted = st.form_submit_button("Executar teste", type="primary")

if submitted:
    st.session_state.pop("execution", None)
    try:
        with st.spinner("Executando o roteiro. O Chromium vai abrir durante o teste."):
            st.session_state.execution = run_scenario(scenario_text)
    except subprocess.TimeoutExpired:
        st.error("O teste ultrapassou 10 minutos.")
    except OSError as error:
        st.error(f"Não foi possível iniciar o teste. {error}")

if "execution" in st.session_state:
    report, details = st.session_state.execution
    if report is None:
        st.error("O ChatProbe não conseguiu concluir a execução.")
    else:
        st.subheader("Conversa")
        for turn in json.loads(report):
            with st.chat_message("user"):
                st.text(turn["message"])
            with st.chat_message("assistant"):
                for reply in turn["replies"]:
                    st.text(reply)
        if "Roteiro interrompido:" in details:
            st.warning("O bot respondeu de forma diferente do esperado pelo roteiro.")
        st.download_button("Baixar JSON", report, file_name="chatprobe.json",
                           mime="application/json")
    if details:
        with st.expander("Detalhes da execução", expanded=report is None):
            st.code(details, language="text")
