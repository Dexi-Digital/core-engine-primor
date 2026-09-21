"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/**
 * Recarrega os dados da página a cada `segundos` enquanto estiver
 * montado. Serve para telas server-side acompanharem um trabalho que
 * roda no worker (ex.: a carga do EasyJur) sem virar client component.
 *
 * Só monte enquanto houver algo em andamento — desmontou, parou.
 */
export function AutoRefresh({ segundos = 15 }: { segundos?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), segundos * 1000);
    return () => clearInterval(id);
  }, [router, segundos]);
  return null;
}
