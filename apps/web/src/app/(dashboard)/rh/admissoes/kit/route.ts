/**
 * Download do kit de admissao.
 *
 * O CSV nao pode sair por Server Action: action devolve dado para o
 * React, nao um arquivo para o browser salvar. E tambem nao pode ser
 * link direto para a API -- o token vive em cookie HTTP-only deste
 * dominio e nao acompanharia a navegacao.
 *
 * Entao este handler faz a ponte: le o cookie, chama a API e repassa o
 * corpo binario com o `Content-Disposition` que a API definiu.
 */
import { NextResponse } from "next/server";

import { getAccessToken } from "@/lib/auth";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const obra = searchParams.get("obra");
  const jornadaId = searchParams.get("jornada_id");

  // Os dois casos sao GET puro: este handler so REBAIXA um kit ja
  // gerado. A geracao (que avanca a etapa) e um POST feito por Server
  // Action na tela -- um link de download nunca pode mover a jornada de
  // ninguem, porque prefetch e refresh o disparam sozinhos.
  const alvo = obra
    ? {
        path: `/api/v1/dp-sesmt/admissao/kit-lote/download?obra=${encodeURIComponent(obra)}`,
        method: "GET",
      }
    : jornadaId
      ? {
          path: `/api/v1/dp-sesmt/admissao/${jornadaId}/kit`,
          method: "GET",
        }
      : null;

  if (!alvo) {
    return NextResponse.json(
      { erro: "informe ?obra= ou ?jornada_id=" },
      { status: 400 },
    );
  }

  const token = await getAccessToken();
  const resp = await fetch(`${API_BASE}${alvo.path}`, {
    method: alvo.method,
    headers: token ? { authorization: `Bearer ${token}` } : {},
  });

  if (!resp.ok) {
    return NextResponse.json(
      { erro: await resp.text() },
      { status: resp.status },
    );
  }

  return new NextResponse(await resp.arrayBuffer(), {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition":
        resp.headers.get("content-disposition") ??
        'attachment; filename="kit-admissao.csv"',
    },
  });
}
