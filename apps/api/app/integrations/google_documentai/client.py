"""Google Document AI -- OCR + extracao estruturada de partes diarias.

Document AI (https://cloud.google.com/document-ai) e o OCR cloud do GCP.
Para parte diaria (PDF + foto manuscrita) usamos o `OCR_PROCESSOR`
generico se quisermos so texto bruto, ou idealmente um
`FORM_PARSER_PROCESSOR` que ja devolve pares chave-valor extraidos.

Sem credenciais (`GOOGLE_DOCUMENTAI_CREDENTIALS_JSON` / `GCP_PROJECT_ID`
/ `DOCUMENTAI_PROCESSOR_ID` vazios), o adapter cai num
`GoogleDocumentAIMockClient` deterministico -- mesmo padrao de
DirectData/LLM/OneDrive/Dominio/Infosimples.

Auth flow real (sem dep de google-auth):
  1. Carrega JSON da service account (`{client_email, private_key, ...}`).
  2. Monta JWT RS256 com claim `scope=cloud-platform` e `aud=oauth2.googleapis.com/token`.
  3. POST `oauth2.googleapis.com/token` (`grant_type=jwt-bearer`) -> access_token.
  4. POST `{location}-documentai.googleapis.com/v1/projects/{project}/locations/{location}/processors/{id}:process`
     com `{rawDocument: {content: base64(file), mimeType: ...}}`.

Resposta normalizada (mesma p/ mock e real):
  {
    "raw_text": "...",
    "fields": {
        "data": "2025-08-15" | None,
        "operador": "Joao da Silva" | None,
        "obra": "Obra Norte" | None,
        "equipamento": "Constellation 24.280" | None,
        "placa": "ABC1234" | None,
        "horimetro_inicio": 1234.5 | None,
        "horimetro_fim": 1278.9 | None,
        "km_inicio": 45000 | None,
        "km_fim": 45230 | None,
    },
    "confidence": 0.94,
    "raw_response": {...},  # resposta original (p/ auditoria/reprocesso)
    "source": "google_documentai" | "google_documentai_mock",
  }
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx
from jose import jwt as jose_jwt

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

DEFAULT_LOCATION = "us"
DEFAULT_OAUTH_AUDIENCE = "https://oauth2.googleapis.com/token"
TOKEN_TTL_SECONDS = 3500  # tokens da Google duram 3600s; renovamos antes
SCOPE_CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"


class DocumentAIError(RuntimeError):
    """Falha ao chamar Document AI (transporte/auth/status nao-2xx)."""


class DocumentAIAuthError(DocumentAIError):
    """Auth nao transitorio -- credenciais invalidas/revogadas."""


class GoogleDocumentAIClient(IntegrationClient):
    """Adapter Document AI real. Sem creds completos -> modo mock.

    Modo mock e ativado quando QUALQUER um dos 3 (credentials_json,
    project_id, processor_id) esta vazio -- nao adianta ter so a
    service account sem o processor configurado.
    """

    name = "google_documentai"

    def __init__(
        self,
        *,
        credentials_json: str | None = None,
        project_id: str | None = None,
        processor_id: str | None = None,
        location: str = DEFAULT_LOCATION,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._credentials_raw = credentials_json or ""
        self._project_id = project_id or ""
        self._processor_id = processor_id or ""
        self._location = location or DEFAULT_LOCATION
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self._service_account: dict[str, Any] | None = None
        if self._credentials_raw and not self.is_mock:
            try:
                self._service_account = json.loads(self._credentials_raw)
            except json.JSONDecodeError as exc:
                # Credenciais malformadas -- preferimos cair em mock
                # (com warning) a quebrar a importacao do servico.
                # Limpa _credentials_raw para is_mock devolver True, senao
                # toda chamada `processar_documento` levantaria
                # DocumentAIAuthError ao inves de cair no mock.
                logger.warning(
                    "GOOGLE_DOCUMENTAI_CREDENTIALS_JSON invalido (%s) "
                    "-- caindo em modo mock",
                    exc,
                )
                self._credentials_raw = ""
                self._service_account = None

    @property
    def is_mock(self) -> bool:
        return not (
            self._credentials_raw
            and self._project_id
            and self._processor_id
        )

    async def health_check(self) -> bool:
        if self.is_mock:
            return True
        try:
            await self._get_access_token()
        except DocumentAIError:
            return False
        return True

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    # --- API publica ------------------------------------------------------

    async def processar_documento(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Processa um arquivo (PDF, JPG, PNG) e devolve campos extraidos.

        `filename` e opcional -- usado so como entrada do hash do mock.
        Em modo real, Document AI nao precisa do nome.
        """
        if self.is_mock:
            return self._mock_response(filename or "arquivo", content)
        if self._service_account is None:
            raise DocumentAIAuthError(
                "credenciais GCP invalidas -- JSON malformado"
            )
        token = await self._get_access_token()
        endpoint = (
            f"https://{self._location}-documentai.googleapis.com"
            f"/v1/projects/{self._project_id}"
            f"/locations/{self._location}"
            f"/processors/{self._processor_id}:process"
        )
        payload = {
            "rawDocument": {
                "content": base64.b64encode(content).decode("ascii"),
                "mimeType": mime_type,
            },
        }
        try:
            r = await self._client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise DocumentAIError(
                f"transporte Document AI: {exc}"
            ) from exc
        if r.status_code in (401, 403):
            raise DocumentAIAuthError(
                f"Document AI auth: status {r.status_code} -- {r.text[:200]}"
            )
        if r.status_code != 200:
            raise DocumentAIError(
                f"Document AI status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise DocumentAIError(
                f"Document AI devolveu nao-JSON: {exc}"
            ) from exc
        return self._normalize(data)

    # --- helpers de auth (JWT bearer flow) -------------------------------

    async def _get_access_token(self) -> str:
        now = time.time()
        if (
            self._access_token
            and self._access_token_expires_at - now > 60
        ):
            return self._access_token
        if self._service_account is None:
            raise DocumentAIAuthError("service account JSON ausente")
        sa = self._service_account
        client_email = sa.get("client_email")
        private_key = sa.get("private_key")
        if not (client_email and private_key):
            raise DocumentAIAuthError(
                "service account sem `client_email` / `private_key`"
            )
        iat = int(now)
        claims = {
            "iss": client_email,
            "scope": SCOPE_CLOUD_PLATFORM,
            "aud": DEFAULT_OAUTH_AUDIENCE,
            "iat": iat,
            "exp": iat + TOKEN_TTL_SECONDS,
        }
        try:
            assertion = jose_jwt.encode(
                claims, private_key, algorithm="RS256"
            )
        except Exception as exc:
            raise DocumentAIAuthError(
                f"falha ao assinar JWT: {exc}"
            ) from exc
        try:
            r = await self._client.post(
                DEFAULT_OAUTH_AUDIENCE,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        except httpx.HTTPError as exc:
            raise DocumentAIError(
                f"transporte oauth2 Google: {exc}"
            ) from exc
        if r.status_code != 200:
            raise DocumentAIAuthError(
                f"oauth2 Google status {r.status_code}: {r.text[:200]}"
            )
        token_data = r.json()
        self._access_token = token_data.get("access_token")
        self._access_token_expires_at = (
            now + int(token_data.get("expires_in", 3600))
        )
        if not self._access_token:
            raise DocumentAIAuthError(
                "oauth2 Google sem `access_token` na resposta"
            )
        return self._access_token

    # --- normalizacao da resposta ---------------------------------------

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extrai `fields` + `raw_text` da resposta do Document AI.

        Document AI devolve `document.entities` (Form Parser) com nome
        + `mentionText` + `confidence`. Mapeamos chaves PT-BR para os
        campos canonicos da `parte_diaria`. Quando o processor for so
        OCR (sem entities), `fields` fica vazio mas `raw_text` continua
        util para revisao manual.
        """
        document = raw.get("document") or {}
        text = document.get("text", "") or ""
        entities = document.get("entities") or []

        # Heuristica de mapping nome-da-entidade -> chave canonica.
        mapping: dict[str, str] = {
            "data": "data",
            "date": "data",
            "operador": "operador",
            "operator": "operador",
            "motorista": "operador",
            "obra": "obra",
            "equipamento": "equipamento",
            "veiculo": "equipamento",
            "placa": "placa",
            "horimetro_inicio": "horimetro_inicio",
            "horimetro_inicial": "horimetro_inicio",
            "horimetro_fim": "horimetro_fim",
            "horimetro_final": "horimetro_fim",
            "km_inicio": "km_inicio",
            "km_inicial": "km_inicio",
            "km_fim": "km_fim",
            "km_final": "km_fim",
        }

        fields: dict[str, Any] = {}
        confidences: list[float] = []
        for entity in entities:
            name = (entity.get("type") or "").strip().lower()
            value = (
                entity.get("mentionText")
                or entity.get("normalizedValue", {}).get("text")
                or ""
            ).strip()
            confidence = float(entity.get("confidence") or 0)
            confidences.append(confidence)
            key = mapping.get(name)
            if not key or not value:
                continue
            if key in {"horimetro_inicio", "horimetro_fim"}:
                fields[key] = _parse_float(value)
            elif key in {"km_inicio", "km_fim"}:
                fields[key] = _parse_int(value)
            else:
                fields[key] = value

        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        return {
            "raw_text": text[:50000],  # corta texto monstro
            "fields": fields,
            "confidence": round(avg_confidence, 4),
            "raw_response": raw,
            "source": "google_documentai",
        }

    # --- mock deterministico -------------------------------------------

    def _mock_response(
        self, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Resposta deterministica baseada no hash de filename + tamanho.

        Mesmo arquivo -> mesma resposta. Util para snapshots em CI e
        para a UI ter campos parecidos com o real (datas plausiveis,
        horimetros crescentes, KM positivo).
        """
        digest = hashlib.sha1(
            f"{filename}|{len(content)}".encode()
        ).digest()
        pick = lambda lst, i: lst[digest[i] % len(lst)]  # noqa: E731
        operadores = [
            "Joao da Silva",
            "Pedro Santos",
            "Carlos Pereira",
            "Antonio Souza",
            "Marcos Oliveira",
        ]
        obras = ["Obra Norte", "Obra Centro", "Obra Sul", "Pateo Primor"]
        equipamentos = [
            "VW/CONSTELLATION 24.280",
            "VOLVO/FH 460",
            "MERCEDES/AROCS 4144",
            "CAT/336F (Escavadeira)",
            "CASE/SR175 (Mini-carregadeira)",
        ]
        placas = ["ABC1234", "XYZ9876", "BRA2A23", "MGV5C12", "GOI8X45"]
        # Data plausivel (ultimos 60 dias)
        dia_offset = digest[8] % 60
        # Construimos a data via aritmetica simples para nao depender
        # de `datetime.now()` (mock precisa ser deterministico em CI).
        ano = 2025
        mes = (digest[9] % 12) or 1
        data_str = f"{ano:04d}-{mes:02d}-{((dia_offset % 27) + 1):02d}"
        horimetro_ini = 1000.0 + (digest[11] % 500)
        horimetro_fim = horimetro_ini + ((digest[12] % 12) + 1)
        km_ini = 40000 + (digest[13] * 100)
        km_fim = km_ini + ((digest[14] % 200) + 30)
        operador = pick(operadores, 0)
        obra = pick(obras, 1)
        equipamento = pick(equipamentos, 2)
        placa = pick(placas, 3)

        # Texto bruto simulando OCR de uma parte diaria.
        raw_text = (
            "PARTE DIARIA\n"
            f"Data: {data_str}\n"
            f"Operador: {operador}\n"
            f"Obra: {obra}\n"
            f"Equipamento: {equipamento} (placa {placa})\n"
            f"Horimetro inicial: {horimetro_ini:.1f}\n"
            f"Horimetro final: {horimetro_fim:.1f}\n"
            f"KM inicial: {km_ini}\n"
            f"KM final: {km_fim}\n"
            "Atividades: terraplanagem trecho 12+200 a 13+800.\n"
        )
        fields = {
            "data": data_str,
            "operador": operador,
            "obra": obra,
            "equipamento": equipamento,
            "placa": placa,
            "horimetro_inicio": horimetro_ini,
            "horimetro_fim": horimetro_fim,
            "km_inicio": km_ini,
            "km_fim": km_fim,
        }
        return {
            "raw_text": raw_text,
            "fields": fields,
            "confidence": 0.92,
            "raw_response": None,
            "source": "google_documentai_mock",
        }


# --- helpers -----------------------------------------------------------------


_NUM_RE = re.compile(r"[-+]?\d*[.,]?\d+")


def _parse_float(value: str) -> float | None:
    m = _NUM_RE.search(value or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    m = _NUM_RE.search(value or "")
    if not m:
        return None
    try:
        return int(float(m.group(0).replace(",", ".")))
    except ValueError:
        return None
