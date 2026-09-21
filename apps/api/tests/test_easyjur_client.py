"""Adapter do EasyJur.

Nao e RPA de navegador: o login e `POST /acesso/api/login.php` com JSON
de resposta, sem captcha nem 2FA (inspecionado em 17/09/2026). Testes
usam transporte mock -- nenhum toca o EasyJur real, porque cada
tentativa errada la aproxima o BLOQUEIO da conta de quem usa o sistema
para trabalhar.
"""
from __future__ import annotations

import httpx
import pytest

from app.integrations.easyjur.client import (
    EasyjurAuthError,
    EasyjurBloqueioIminenteError,
    EasyjurClient,
    EasyjurError,
)

_RECUSA = {
    "status": 400,
    "mensagem": "",
    "erros": {
        "codigo": 7,
        "mensagem": (
            "Você digitou a senha incorreta 1 vez(es) para este login. <br> "
            "Por favor, tente novamente. <br> Após 5 tentativas consecutivas, "
            "este login será bloqueado."
        ),
        "tentativas_restantes": {"email": 4, "ip": 2},
    },
}


def _client(handler) -> EasyjurClient:
    return EasyjurClient(
        email="x@y.com",
        password="s",
        client=httpx.AsyncClient(
            base_url="https://app.easyjur.com",
            transport=httpx.MockTransport(handler),
        ),
    )


@pytest.mark.asyncio
async def test_login_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            assert b"email=" in request.content
            return httpx.Response(200, json={"status": 200})
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    await c.login()
    assert c.tentativas_restantes is None


@pytest.mark.asyncio
async def test_login_recusado_expoe_tentativas_restantes():
    """O menor contador entre email e IP e o que importa -- e quem
    bloqueia primeiro."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            return httpx.Response(400, json=_RECUSA)
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    with pytest.raises(EasyjurAuthError) as exc:
        await c.login()
    assert exc.value.tentativas_restantes == 2  # min(email=4, ip=2)
    assert "senha incorreta" in str(exc.value)
    # `<br>` da mensagem deles nao vaza para o log
    assert "<br>" not in str(exc.value)


@pytest.mark.asyncio
async def test_recusa_preventiva_quando_falta_uma_tentativa():
    """A garantia central: o adapter NAO gasta a ultima tentativa.

    Bloquear a conta do cliente para descobrir que a senha esta errada
    e o pior desfecho possivel -- e o erro ja e conhecido.
    """
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            chamadas["n"] += 1
            recusa = {
                **_RECUSA,
                "erros": {
                    **_RECUSA["erros"],
                    "tentativas_restantes": {"email": 1, "ip": 1},
                },
            }
            return httpx.Response(400, json=recusa)
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    with pytest.raises(EasyjurAuthError):
        await c.login()
    assert chamadas["n"] == 1

    # segunda chamada NAO deve chegar ao EasyJur
    with pytest.raises(EasyjurBloqueioIminenteError):
        await c.login()
    assert chamadas["n"] == 1, "gastou tentativa que deveria ter evitado"


@pytest.mark.asyncio
async def test_login_e_idempotente():
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            chamadas["n"] += 1
            return httpx.Response(200, json={"status": 200})
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    await c.login()
    await c.login()
    assert chamadas["n"] == 1


@pytest.mark.asyncio
async def test_resposta_nao_json_vira_erro_de_transporte():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>manutencao</html>")

    c = _client(handler)
    with pytest.raises(EasyjurError):
        await c.login()


@pytest.mark.asyncio
async def test_health_check_nao_autentica():
    """Health check que faz login gastaria tentativa a cada verificacao
    -- um painel de status nao pode bloquear a conta do cliente."""
    tocou_login = {"v": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            tocou_login["v"] = True
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    assert await c.health_check() is True
    assert tocou_login["v"] is False


@pytest.mark.asyncio
async def test_sem_credencial_e_mock_e_nao_faz_rede():
    c = EasyjurClient()
    assert c.is_mock is True
    assert await c.health_check() is False
    with pytest.raises(EasyjurAuthError):
        await c.login()
    await c.aclose()


# --- leitura ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_listar_processos_manda_acao_listagem():
    """SEM `acao_listagem=enviar` o EasyJur devolve 200 com "0 Registros
    Encontrados". Foi assim que 453 processos viraram "0 na base" em
    17/09/2026 -- o parametro nao pode sumir numa refatoracao."""
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            return httpx.Response(200, json={"status": 200})
        if request.url.path.endswith("ajax_processos_lista.php"):
            visto["body"] = request.content.decode()
            return httpx.Response(200, text="<table></table>")
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    await c.listar_processos(page=3)
    assert "acao_listagem=enviar" in visto["body"]
    assert "page=3" in visto["body"]


@pytest.mark.asyncio
async def test_export_arma_o_filtro_da_sessao_antes():
    """O export le o filtro da sessao PHP: chamado direto devolve so o
    cabecalho. A busca com `pesquisa=enviar` tem de vir ANTES, na mesma
    sessao."""
    ordem: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/api/login.php"):
            return httpx.Response(200, json={"status": 200})
        if p.endswith("ajax_andamento_lista.php"):
            ordem.append("busca:" + request.content.decode())
            return httpx.Response(200, text="<table></table>")
        if p.endswith("export_andamentos.php"):
            ordem.append("export")
            return httpx.Response(200, content=b'"ID";\r\n')
        return httpx.Response(200, text="<form>")

    c = _client(handler)
    await c.exportar_andamentos_csv()
    assert len(ordem) == 2
    assert ordem[0].startswith("busca:") and "pesquisa=enviar" in ordem[0]
    assert ordem[1] == "export"


@pytest.mark.asyncio
async def test_leitura_faz_um_login_so():
    """Reautenticar por pagina gastaria tentativa a toa num sistema que
    bloqueia a conta em 5 erros."""
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login.php"):
            logins["n"] += 1
            return httpx.Response(200, json={"status": 200})
        return httpx.Response(200, text="<table></table>")

    c = _client(handler)
    for page in (1, 2, 3):
        await c.listar_processos(page=page)
    assert logins["n"] == 1
