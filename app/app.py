"""Interface local para executar roteiros do ChatProbe."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from src.chatprobe.browser.timing import local_time
from src.chatprobe.desktop_preferences import load_preferences
from src.chatprobe.runner.reports import execution_summary

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/prompts/cupom-nao-impresso.json"


def run_scenario(scenario_text):
    with TemporaryDirectory(prefix="chatprobe-") as directory:
        scenario = Path(directory) / "scenario.json"
        report = Path(directory) / "report.json"
        scenario.write_text(scenario_text, encoding="utf-8")
        process = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-m", "src.chatprobe.runner.scripted",
                load_preferences().chat_url,
                "--scenario", str(scenario), "--output", str(report),
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
        if not report.is_file():
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
        turns = json.loads(report)
        metadata = turns[0].get("test_metadata", {}) if turns else {}
        if metadata:
            st.text(execution_summary(metadata))
        if metadata.get("status") == "error":
            st.error("O teste foi encerrado com erro técnico. A conversa parcial foi preservada.")
        for turn in turns:
            with st.chat_message("user"):
                if turn.get("sent_at"):
                    st.caption(local_time(turn["sent_at"]))
                st.text(turn["message"])
            with st.chat_message("assistant"):
                timestamps = turn.get("reply_received_at", [])
                for index, reply in enumerate(turn["replies"]):
                    if index < len(timestamps) and timestamps[index]:
                        st.caption(local_time(timestamps[index]))
                    st.text(reply)
        if "Roteiro interrompido:" in details:
            st.warning("O bot respondeu de forma diferente do esperado pelo roteiro.")
        st.download_button("Baixar JSON", report, file_name="chatprobe.json",
                           mime="application/json")
    if details:
        with st.expander("Detalhes da execução", expanded=report is None):
            st.code(details, language="text")
