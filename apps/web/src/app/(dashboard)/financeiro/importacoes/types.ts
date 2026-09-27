export type Lote = {
  id: number; nome_arquivo: string; origem: string; status: string; actor: string;
  linhas: number; erros: number; avisos: number; mensagem: string | null;
  criado_em: string; atualizado_em: string;
  ocorrencias: { linha: number; tipo: string; mensagem: string }[];
  resumo: { empresa: string; linhas: number; valor_apropriado: string }[];
};
export type Registro = Record<string, string | number | null>;
export type Previa = { total: number; valor_apropriado: string; data: Registro[]; lote_id: number | null };
export type Totvs = {
  total: number; valor_original: string;
  data: {
    external_id: string; codcoligada: number | null; codfilial: number | null; idlan: number | null;
    contraparte_documento: string | null; contraparte_nome: string | null; data_emissao: string | null;
    data_vencimento: string | null; valor: string | null; valor_baixado: string | null;
    saldo: string | null; status_rm: string | null; situacao: string | null; extractor: string;
  }[];
  sincronizacoes: {
    source: string; janela: string; extractor: string; status: string; lidos: number; gravados: number;
    error_message: string | null; started_at: string | null; finished_at: string | null;
  }[];
};
export type Resultado<T> = { data: T; error?: never } | { error: string; data?: never };
