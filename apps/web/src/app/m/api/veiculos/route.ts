/**
 * Lista enxuta de veiculos para popular o dropdown do PWA mobile.
 * Reutiliza GET /api/v1/manutencao-frota/veiculos com page_size=200,
 * filtra somente os ativos (status=ativo) -- veiculo baixado nao
 * deve aparecer pro apontador em campo.
 */
import { NextResponse } from "next/server";

import { apiFetch, ApiError } from "@/lib/api";

type Veiculo = {
  id: number;
  placa: string;
  marca: string | null;
  modelo: string | null;
  status: string | null;
};

type VeiculoListResponse = {
  items: Veiculo[];
  total: number;
};

export async function GET() {
  try {
    const data = await apiFetch<VeiculoListResponse>(
      "/api/v1/manutencao-frota/veiculos?page=1&page_size=200&status=ativo",
    );
    return NextResponse.json({
      items: data.items.map((v) => ({
        id: v.id,
        placa: v.placa,
        descricao: [v.marca, v.modelo].filter(Boolean).join(" "),
      })),
    });
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(
        { detail: err.body },
        { status: err.status },
      );
    }
    return NextResponse.json({ detail: String(err) }, { status: 500 });
  }
}
