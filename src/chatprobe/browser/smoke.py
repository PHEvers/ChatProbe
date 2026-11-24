"""Verifica a triagem do AutoSystem e executa mensagens ou um roteiro."""

import argparse
import json
import re
from pathlib import Path
from time import monotonic
from unicodedata import normalize

if __package__:
    from .replies import LISTEN_FOR_SUPPORT, ReplyCapture, message_text_key
else:
    from replies import LISTEN_FOR_SUPPORT, ReplyCapture, message_text_key


DEFAULT_REPLY_SELECTOR = "p[lwc-20emgi50k8t]"
DEFAULT_PRODUCT = "AUTOSYSTEM"
PRODUCT_OPTIONS = (
    DEFAULT_PRODUCT, "SELLER", "Emsys", "Posto Facil", "Autosystem Ipiranga",
    "EMSYS", "Emporio", "D-TEF", "QR LINX", "CONECTIVIDADE",
)
DEFAULT_CUSTOMER_NAME = "Pedro Teste Automatizado"
NAME_REQUEST = re.compile(
    r"\b(?:"
    r"(?:qual(?: e)?|informe|informar|digite|digitar|forneca|fornecer|diga|dizer|envie|enviar|"
    r"confirme|confirmar|preciso d[eo]) (?:o )?(?:seu|teu) nome"
    r"|(?:qual(?: e)?|informe|informar|digite|digitar|forneca|fornecer) (?:o )?nome completo"
    r"|(?:seu|teu) nome(?: completo)?[\s,]*(?:por favor|\?)"
    r"|como (?:posso|podemos|devo) (?:te chamar|chamar voce|chama-l[oa]|lhe chamar)"
    r")(?!\w)"
)


def load_scenario(path):
    return parse_scenario(json.loads(path.read_text(encoding="utf-8")))


def parse_scenario(data):
    if not isinstance(data, dict):
        raise ValueError("O roteiro deve ser um objeto JSON.")
    initial = data.get("initial_message")
    steps = data.get("steps")
    if not isinstance(initial, str) or not initial.strip() or not isinstance(steps, list):
        raise ValueError("Informe initial_message e uma lista steps.")
    rules = []
    for number, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or any(
            not isinstance(step.get(key), str) or not step[key].strip()
            for key in ("when_contains", "message")
        ):
            raise ValueError(f"Passo {number}: informe when_contains e message.")
        rules.append((step["when_contains"].strip(), step["message"]))
    return initial, rules


def asks_for_customer_name(replies):
    plain = normalize("NFKD", " ".join(replies).casefold()).encode("ascii", "ignore").decode("ascii")
    return NAME_REQUEST.search(" ".join(plain.split())) is not None


def planned_messages(messages, rules, turns, log, customer_name=None):
    for number, message in enumerate(messages):
        if rules and number:
            expected_text, _ = rules[number - 1]
            replies = turns[-1]["replies"]
            last_reply = " ".join(replies)
            if expected_text.casefold() not in last_reply.casefold():
                log(f'Roteiro interrompido: a resposta não contém "{expected_text}".')
                return
            if customer_name and asks_for_customer_name(replies):
                message = customer_name
        yield message


def guided_messages(next_message, turns):
    while (message := next_message(turns)) is not None:
        yield message


def wait_for_triage_step(destination, other_subject, log):
    visible_destination = destination.filter(visible=True)
    visible_destination.or_(other_subject).first.wait_for(state="visible", timeout=60_000)
    if visible_destination.count():
        return
    other_subject.last.click(timeout=60_000)
    log("Há casos em aberto. A opção Outro assunto foi selecionada.")
    visible_destination.wait_for(state="visible", timeout=60_000)


def main(argv=None, log=print, *, scenario=None, next_message=None, turns=None, checkpoint=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Endereço do chat de homologação")
    parser.add_argument("--cnpj", default="13041197000159",
                        help="CNPJ de homologação do AutoSystem (somente números)")
    parser.add_argument("--product", default=DEFAULT_PRODUCT, help="Produto selecionado na triagem")
    parser.add_argument("--customer-name", help="Nome enviado nas etapas do roteiro que pedem o nome")
    parser.add_argument("--setup-only", action="store_true",
                        help="Pare quando o bot pedir o motivo do contato")
    parser.add_argument("--reply-selector",
                        help="Seletor de cada balão do bot, dentro do iframe")
    parser.add_argument("--scenario", type=Path, help="Arquivo JSON com o roteiro")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Encerre com diagnóstico se a abertura exigir intervenção")
    parser.add_argument("--headless", action="store_true", help="Execute o Chromium sem janela")
    parser.add_argument("--message", action="append", default=[],
                        help="Mensagem inicial. Se omitida, a segunda mensagem usa "
                             "Pedro Teste Automatizado.")
    parser.add_argument("--output", type=Path, default=Path("reports/smoke.json"))
    args = parser.parse_args(argv)
    args.non_interactive = args.non_interactive or args.headless
    checkpoint = checkpoint if checkpoint is not None else lambda: None
    use_events = not args.setup_only and not args.reply_selector
    rules = []
    if next_message is not None:
        messages = []
        args.reply_selector = args.reply_selector or DEFAULT_REPLY_SELECTOR
    elif args.scenario or scenario is not None:
        if args.setup_only or args.message:
            parser.error("--scenario deve ser usado sem --message e sem --setup-only.")
        try:
            initial, rules = scenario if scenario is not None else load_scenario(args.scenario)
        except (OSError, ValueError) as error:
            parser.error(f"Roteiro inválido: {error}")
        messages = [initial, *(message for _, message in rules)]
        args.reply_selector = args.reply_selector or DEFAULT_REPLY_SELECTOR
    elif not args.setup_only and (
        not args.reply_selector
        or len(args.message) not in (1, 2)
        or any(not message.strip() for message in args.message)
    ):
        parser.error("Informe --reply-selector e uma ou duas mensagens, ou use --setup-only.")
    else:
        messages = args.message
        if len(messages) == 1:
            messages = [*messages, args.customer_name or DEFAULT_CUSTOMER_NAME]

    cnpj = args.cnpj.strip()
    if not re.fullmatch(r"[0-9]{14}", cnpj):
        parser.error("Informe um CNPJ de teste com 14 dígitos.")
    product = args.product.strip()
    if not product:
        parser.error("Informe o produto para a triagem.")

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import expect, sync_playwright

    if turns is None:
        turns = []
    checkpoint()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium" if args.headless else None,
                                             headless=args.headless)
        try:
            page = browser.new_page()
            if use_events:
                page.add_init_script(LISTEN_FOR_SUPPORT)
            page.goto(args.url, wait_until="domcontentloaded")
            consent = page.get_by_role("button", name="Aceitar", exact=True)
            launcher = page.get_by_role(
                "button", name=re.compile(r"olá,?\s*tem alguma pergunta", re.I)
            ).or_(page.locator("button#embeddedMessagingConversationButton"))
            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                checkpoint()
                deadline = monotonic() + 10
                try:
                    consent.or_(launcher).filter(visible=True).first.wait_for(
                        state="visible", timeout=10_000
                    )
                    if consent.is_visible():
                        consent.click(timeout=max(1, (deadline - monotonic()) * 1000))
                    launcher.filter(visible=True).first.wait_for(
                        state="visible", timeout=max(1, (deadline - monotonic()) * 1000)
                    )
                    break
                except PlaywrightTimeoutError:
                    if attempt == max_attempts:
                        log(f"Página aberta: {page.title() or '(sem título)'}")
                        log(f"Aviso de cookies visível: {consent.is_visible()}")
                        log(f"Botões de abertura encontrados: {launcher.count()}")
                        if args.non_interactive:
                            raise SystemExit("O balão do chat não apareceu após as recargas.")
                        input("Recarregue no Chromium. Quando o balão aparecer, "
                              "volte ao terminal e pressione Enter. ")
                        if consent.is_visible():
                            consent.click(timeout=10_000)
                        if not launcher.filter(visible=True).count():
                            raise SystemExit("O balão do chat continua indisponível.")
                        break
                    log(f"Balão indisponível. Recarregando a página ({attempt}/{max_attempts - 1})...")
                    checkpoint()
                    page.reload(wait_until="domcontentloaded")
            checkpoint()
            launcher.filter(visible=True).first.click(timeout=15_000)
            frame_selector = (
                'iframe[name="embeddedMessagingFrame"]:visible, '
                'iframe[title="Bate-papo com um agente"]:visible'
            )
            chat = page.frame_locator(frame_selector)
            textbox = chat.get_by_role("textbox", name="Digite sua mensagem...")
            send = chat.get_by_role("button", name="Enviar mensagem")
            other_subject = chat.get_by_role("option", name="Outro assunto", exact=True).or_(
                chat.get_by_role("button", name="Outro assunto", exact=True)
            ).filter(visible=True)

            stage = "o campo de mensagem do chat"
            try:
                textbox.wait_for(state="visible", timeout=30_000)
                stage = "a primeira mensagem pedindo o CNPJ"
                chat.get_by_text(
                    re.compile(r"(?:fornecer o CNPJ|CNPJ\s*\(somente números\))", re.I)
                ).first.wait_for(state="visible", timeout=60_000)
            except PlaywrightTimeoutError:
                log(f"Não encontrei {stage}.")
                log(f"Iframes visíveis do chat: {page.locator(frame_selector).count()}")
                if not args.non_interactive:
                    input("Confira o painel no Chromium e pressione Enter para fechar. ")
                raise SystemExit("A triagem parou antes do envio do CNPJ.") from None
            checkpoint()
            textbox.fill(cnpj)
            send.click()
            support = chat.get_by_role("option", name="Suporte Técnico", exact=True)
            wait_for_triage_step(support, other_subject, log)
            checkpoint()
            support.click(timeout=60_000)
            exact_product = product in PRODUCT_OPTIONS
            product_label = product if exact_product else re.compile(
                rf"^{re.escape(product)}$", re.IGNORECASE,
            )
            try:
                product_option = chat.get_by_role("option", name=product_label, exact=exact_product)
                wait_for_triage_step(product_option, other_subject, log)
                checkpoint()
                product_option.click(timeout=60_000)
            except PlaywrightTimeoutError:
                raise SystemExit(f'O produto "{product}" não apareceu após Suporte Técnico.') from None
            reason = chat.get_by_text(re.compile(r"motivo do seu contato", re.I))
            wait_for_triage_step(reason, other_subject, log)
            log("Triagem concluída. O bot pediu o motivo do contato.")
            if args.setup_only:
                if not args.non_interactive:
                    input("Pressione Enter para fechar o navegador. ")
                return

            replies = chat.locator(args.reply_selector).filter(visible=True)
            capture = ReplyCapture(page, replies, use_events=use_events)
            expect(textbox).to_be_editable()

            conversation = (guided_messages(next_message, turns) if next_message is not None
                            else planned_messages(messages, rules, turns, log, args.customer_name))
            outgoing = {message_text_key(cnpj)}
            for number, message in enumerate(conversation, start=1):
                checkpoint()
                outgoing.add(message_text_key(message))
                textbox.fill(message)
                before = capture.snapshot(outgoing)
                send.click()
                turn = {"message": message, "replies": []}
                turns.append(turn)
                log(f"Turno {number}: aguardando a resposta do bot...")
                turn["replies"] = capture.wait_for_reply(before, outgoing)
                log(f"Turno {number}: resposta do suporte capturada.")
        finally:
            browser.close()

    if next_message is not None or scenario is not None:
        return turns
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(turns, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    if argv is None:
        log(f"Respostas salvas em {args.output}")


if __name__ == "__main__":
    main()
