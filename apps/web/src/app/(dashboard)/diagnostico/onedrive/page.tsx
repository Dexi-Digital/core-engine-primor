import { apiFetch, ApiError } from "@/lib/api";

import { DiagnosticoView, type DiagnosticoResponse } from "./view";

export const dynamic = "force-dynamic";

type AreaKey = "all" | "dp" | "frota" | "obras" | "empresa";

type PageProps = {
  searchParams: Promise<{ area?: string }>;
};

function normalizeArea(raw: string | undefined): AreaKey {
  switch (raw) {
    case "dp":
    case "frota":
    case "obras":
    case "empresa":
      return raw;
    default:
      return "all";
  }
}

async function fetchDiagnostico(
  area: AreaKey,
): Promise<{ data: DiagnosticoResponse | null; error: string | null }> {
  try {
    const data = await apiFetch<DiagnosticoResponse>(
      `/api/v1/diagnostico/onedrive?area=${encodeURIComponent(area)}`,
    );
    return { data, error: null };
  } catch (err) {
    if (err instanceof ApiError) {
      return { data: null, error: `API ${err.status}: ${err.body.slice(0, 500)}` };
    }
    return { data: null, error: String(err) };
  }
}

export default async function OneDriveDiagnosticoPage(props: PageProps) {
  const sp = await props.searchParams;
  const area = normalizeArea(sp.area);
  const { data, error } = await fetchDiagnostico(area);

  return <DiagnosticoView result={data} area={area} error={error} />;
}
